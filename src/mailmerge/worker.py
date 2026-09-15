from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from .config import settings
from .db import SessionLocal, init_db
from .messages import build_message
from .models import AuditLog, Campaign, CampaignState, DeliveryAttempt, Profile, Recipient, recipient_domain_ordering
from .profile_config import load_profiles
from .rendering import render_message, templates_for_unsubscribe_setting
from .secrets import get_secret
from .smtp import AuthenticationFailure, classify_smtp_error, connect, send

RETRY_DELAYS = (60, 300, 900)


def _dispatch_timezone(campaign: Campaign, profile: Profile | None) -> ZoneInfo:
    tz_name = campaign.working_hours_timezone or (profile.working_hours_timezone if profile else "UTC") or "UTC"
    try:
        return ZoneInfo(tz_name)
    except Exception:
        return ZoneInfo("UTC")


def _window_minutes(campaign: Campaign, profile: Profile | None) -> tuple[int, int]:
    start = campaign.working_hours_start * 60 + (campaign.working_hours_start_minute or 0)
    end = campaign.working_hours_end * 60 + (campaign.working_hours_end_minute or 0)
    return start, end


def is_within_working_hours(campaign: Campaign, profile: Profile | None, now_utc: datetime | None = None) -> bool:
    """Whether now is inside the configured daily dispatch window (every day)."""
    tz = _dispatch_timezone(campaign, profile)
    start_minutes, end_minutes = _window_minutes(campaign, profile)
    if start_minutes >= end_minutes:
        return False

    current_local = (now_utc or datetime.now(timezone.utc)).astimezone(tz)
    current_minutes = current_local.hour * 60 + current_local.minute
    return start_minutes <= current_minutes < end_minutes


def next_dispatch_start(campaign: Campaign, profile: Profile, now_utc: datetime | None = None) -> datetime:
    tz = _dispatch_timezone(campaign, profile)
    start_minutes, end_minutes = _window_minutes(campaign, profile)
    current = (now_utc or datetime.now(timezone.utc)).astimezone(tz)
    candidate = current.replace(hour=start_minutes // 60, minute=start_minutes % 60, second=0, microsecond=0)
    if current >= current.replace(hour=end_minutes // 60, minute=end_minutes % 60, second=0, microsecond=0):
        candidate += timedelta(days=1)
    elif current >= candidate:
        return current.astimezone(timezone.utc)
    return candidate.astimezone(timezone.utc)


def sent_today(db, profile: Profile, campaign: Campaign, now_utc: datetime | None = None) -> int:
    tz = _dispatch_timezone(campaign, profile)
    current = (now_utc or datetime.now(timezone.utc)).astimezone(tz)
    start = current.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    end = start + timedelta(days=1)
    return db.scalar(
        select(func.count()).select_from(DeliveryAttempt)
        .join(Recipient, DeliveryAttempt.recipient_id == Recipient.id)
        .join(Campaign, Recipient.campaign_id == Campaign.id)
        .where(Campaign.profile_id == profile.id, DeliveryAttempt.outcome == "sent", DeliveryAttempt.attempted_at >= start, DeliveryAttempt.attempted_at < end)
    ) or 0


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
                ).order_by(*recipient_domain_ordering())
            ).all()

            effective_delay = campaign.delay_seconds if campaign.delay_seconds is not None else 2

            for recipient in recipients:
                db.refresh(campaign)
                if campaign.state != CampaignState.sending:
                    break
                if not is_within_working_hours(campaign, profile):
                    campaign.state = CampaignState.scheduled
                    campaign.scheduled_at = next_dispatch_start(campaign, profile)
                    db.commit()
                    break
                if sent_today(db, profile, campaign) >= profile.daily_cap:
                    tz = _dispatch_timezone(campaign, profile)
                    start_minutes, _ = _window_minutes(campaign, profile)
                    tomorrow = (datetime.now(timezone.utc).astimezone(tz) + timedelta(days=1)).replace(
                        hour=start_minutes // 60, minute=start_minutes % 60, second=0, microsecond=0
                    )
                    campaign.state = CampaignState.scheduled
                    campaign.scheduled_at = tomorrow.astimezone(timezone.utc)
                    db.commit()
                    break

                attempts = db.scalar(
                    select(DeliveryAttempt).where(DeliveryAttempt.recipient_id == recipient.id).order_by(DeliveryAttempt.id.desc())
                )
                if attempts and attempts.retry_at and attempts.retry_at > datetime.now(timezone.utc):
                    continue
                try:
                    values = dict(recipient.values)
                    values.setdefault("email", recipient.email)
                    subject_template, body_template = templates_for_unsubscribe_setting(
                        campaign.subject_template,
                        campaign.body_template,
                        campaign.list_unsubscribe_enabled,
                    )
                    if campaign.list_unsubscribe_enabled:
                        secret = os.getenv("UNSUBSCRIBE_SIGNING_SECRET") or settings.unsubscribe_signing_secret or ""
                        if secret:
                            from unsubscribe_service.main import sign_token
                            token = sign_token(campaign.id, recipient.email, secret=secret)
                            raw_base = campaign.unsubscribe_base_url or "https://unsub.plus.bi"
                            if raw_base.rstrip("/") == "https://mailmerge.plus.bi":
                                raw_base = "https://unsub.plus.bi"
                            if "/u/" in raw_base:
                                raw_base = raw_base.split("/u/", 1)[0]
                            values.setdefault("unsubscribe_url", f"{raw_base.rstrip('/')}/u/{token}")
                    rendered = render_message(subject_template, body_template, campaign.body_mode, values)
                    message = build_message(campaign, recipient.email, rendered, profile, recipient.reply_to_message_id, recipient.thread_references)
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
            # A permanent rejection belongs to the recipient, not the whole
            # campaign. Once no recipient remains pending/retrying, the
            # dispatch is terminal and follow-ups may safely use its sent set.
            campaign.state = CampaignState.completed
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
        campaigns = db.scalars(select(Campaign).where(Campaign.state.in_([CampaignState.scheduled, CampaignState.sending]))).all()
        due = []
        for campaign in campaigns:
            scheduled = campaign.scheduled_at
            if scheduled and scheduled.tzinfo is None:
                scheduled = scheduled.replace(tzinfo=timezone.utc)
            if campaign.state == CampaignState.sending:
                due.append(campaign.id)
            elif scheduled and scheduled < now - timedelta(minutes=5):
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
