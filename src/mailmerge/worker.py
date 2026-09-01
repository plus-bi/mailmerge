from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from .config import settings
from .db import SessionLocal, init_db
from .messages import build_message
from .models import AuditLog, Campaign, CampaignState, DeliveryAttempt, Profile, Recipient
from .profile_config import load_profiles
from .rendering import render_message
from .secrets import get_secret
from .smtp import AuthenticationFailure, classify_smtp_error, connect, send
from .suppression import sync_suppressions

RETRY_DELAYS = (60, 300, 900)


def is_within_working_hours(campaign: Campaign, profile: Profile | None, now_utc: datetime | None = None) -> bool:
    enabled = campaign.working_hours_enabled or (profile.working_hours_enabled if profile else False)
    if not enabled:
        return True

    tz_name = campaign.working_hours_timezone or (profile.working_hours_timezone if profile else "UTC") or "UTC"
    start_hour = campaign.working_hours_start if campaign.working_hours_start is not None else (profile.working_hours_start if profile else 9)
    end_hour = campaign.working_hours_end if campaign.working_hours_end is not None else (profile.working_hours_end if profile else 17)

    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("UTC")

    current_local = (now_utc or datetime.now(timezone.utc)).astimezone(tz)

    # Monday is 0, Sunday is 6
    if current_local.weekday() >= 5:
        return False

    if start_hour <= end_hour:
        return start_hour <= current_local.hour < end_hour
    else:
        return current_local.hour >= start_hour or current_local.hour < end_hour


def process_campaign(campaign_id: str) -> None:
    with SessionLocal() as db:
        campaign = db.get(Campaign, campaign_id)
        if not campaign or campaign.state not in {CampaignState.scheduled, CampaignState.sending}:
            return
        profile = db.get(Profile, campaign.profile_id)
        if not profile:
            campaign.state = CampaignState.failed
            db.commit()
            return

        if not is_within_working_hours(campaign, profile):
            return

        campaign.state = CampaignState.sending
        db.commit()
        try:
            client = connect(profile, password=get_secret(profile.id, "password"), access_token=get_secret(profile.id, "access_token"))
        except AuthenticationFailure:
            campaign.state = CampaignState.paused
            db.add(AuditLog(campaign_id=campaign.id, action="authentication-failed"))
            db.commit()
            return
        try:
            recipients = db.scalars(
                select(Recipient).where(
                    Recipient.campaign_id == campaign.id,
                    Recipient.included,
                    Recipient.valid,
                    ~Recipient.suppressed,
                    Recipient.status.in_(["pending", "retry"]),
                )
            ).all()

            effective_delay = campaign.delay_seconds if campaign.delay_seconds is not None else profile.delay_seconds

            for recipient in recipients:
                db.refresh(campaign)
                if campaign.state != CampaignState.sending:
                    break
                if not is_within_working_hours(campaign, profile):
                    break

                attempts = db.scalar(
                    select(DeliveryAttempt).where(DeliveryAttempt.recipient_id == recipient.id).order_by(DeliveryAttempt.id.desc())
                )
                if attempts and attempts.retry_at and attempts.retry_at > datetime.now(timezone.utc):
                    continue
                try:
                    values = dict(recipient.values)
                    values.setdefault("email", recipient.email)
                    if campaign.list_unsubscribe_enabled or campaign.purpose == "marketing":
                        secret = os.getenv("UNSUBSCRIBE_SIGNING_SECRET") or settings.unsubscribe_signing_secret or ""
                        if secret:
                            from unsubscribe_service.main import sign_token
                            token = sign_token(campaign.name, recipient.email, secret=secret)
                            raw_base = campaign.unsubscribe_base_url or "https://unsub.plus.bi"
                            if raw_base.rstrip("/") == "https://mailmerge.plus.bi":
                                raw_base = "https://unsub.plus.bi"
                            if "/u/" in raw_base:
                                raw_base = raw_base.split("/u/", 1)[0]
                            values.setdefault("unsubscribe_url", f"{raw_base.rstrip('/')}/u/{token}")
                    rendered = render_message(campaign.subject_template, campaign.body_template, campaign.body_mode, values)
                    message = build_message(campaign, recipient.email, rendered, profile)
                    send(client, message)
                    recipient.status = "sent"
                    recipient.message_id = message["Message-ID"]
                    recipient.sent_at = datetime.now(timezone.utc)
                    db.add(DeliveryAttempt(recipient_id=recipient.id, outcome="sent"))
                except Exception as exc:
                    kind, code = classify_smtp_error(exc)
                    attempt_no = len(db.scalars(select(DeliveryAttempt).where(DeliveryAttempt.recipient_id == recipient.id)).all()) + 1
                    if kind == "transient" and attempt_no <= len(RETRY_DELAYS):
                        recipient.status = "retry"
                        retry_at = datetime.now(timezone.utc) + timedelta(seconds=RETRY_DELAYS[attempt_no - 1])
                    else:
                        recipient.status = "failed"
                        retry_at = None
                    db.add(
                        DeliveryAttempt(
                            recipient_id=recipient.id,
                            outcome=kind,
                            smtp_code=code,
                            detail=str(exc)[:1000],
                            retry_at=retry_at,
                        )
                    )
                db.commit()
                if effective_delay:
                    time.sleep(effective_delay)
        finally:
            try:
                client.quit()
            except Exception:
                pass
        pending = db.scalar(
            select(Recipient).where(
                Recipient.campaign_id == campaign.id,
                Recipient.included,
                Recipient.valid,
                ~Recipient.suppressed,
                Recipient.status.in_(["pending", "retry"]),
            ).limit(1)
        )
        if campaign.state == CampaignState.sending and not pending:
            failed_count = db.scalar(
                select(func.count())
                .select_from(Recipient)
                .where(
                    Recipient.campaign_id == campaign.id,
                    Recipient.included,
                    Recipient.valid,
                    ~Recipient.suppressed,
                    Recipient.status == "failed",
                )
            ) or 0
            campaign.state = CampaignState.failed if failed_count else CampaignState.completed
            db.add(
                AuditLog(
                    campaign_id=campaign.id,
                    action=campaign.state.value,
                    detail={"failed_recipients": failed_count},
                )
            )
            db.commit()


def tick() -> None:
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        # Auto-sync suppressions on each worker tick
        try:
            sync_suppressions(db)
        except Exception:
            pass

        campaigns = db.scalars(select(Campaign).where(Campaign.state == CampaignState.scheduled)).all()
        due = []
        for campaign in campaigns:
            scheduled = campaign.scheduled_at
            if scheduled and scheduled.tzinfo is None:
                scheduled = scheduled.replace(tzinfo=timezone.utc)
            if scheduled and scheduled < now - timedelta(minutes=5):
                campaign.state = CampaignState.awaiting_confirmation
                db.add(AuditLog(campaign_id=campaign.id, action="overdue"))
            elif not scheduled or scheduled <= now:
                due.append(campaign.id)
        db.commit()
    for campaign_id in due:
        process_campaign(campaign_id)


def run() -> None:
    init_db()
    if settings.profile_config_path.is_file():
        with SessionLocal() as db:
            load_profiles(settings.profile_config_path, db)
    while True:
        tick()
        time.sleep(settings.worker_poll_seconds)


if __name__ == "__main__":
    run()
