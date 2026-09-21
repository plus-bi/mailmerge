from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from .config import settings
from .db import SessionLocal, init_db
from .messages import build_individual_message, build_message
from .models import AuditLog, Campaign, CampaignState, DeliveryAttempt, ManualSuppressionEvent, Profile, Recipient, ScheduledEmail, ScheduledEmailAttempt, UnsubscribeEvent, recipient_domain_ordering
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


def _profile_attempt_timestamps(db, profile: Profile, now_utc: datetime) -> list[datetime]:
    start = now_utc - timedelta(hours=24)
    campaign_attempts = db.scalars(
        select(DeliveryAttempt.attempted_at)
        .join(Recipient, DeliveryAttempt.recipient_id == Recipient.id)
        .join(Campaign, Recipient.campaign_id == Campaign.id)
        .where(Campaign.profile_id == profile.id, DeliveryAttempt.attempted_at >= start, DeliveryAttempt.attempted_at <= now_utc)
    ).all()
    individual_attempts = db.scalars(
        select(ScheduledEmailAttempt.attempted_at)
        .join(ScheduledEmail, ScheduledEmailAttempt.scheduled_email_id == ScheduledEmail.id)
        .where(
            ScheduledEmail.profile_id == profile.id,
            ScheduledEmailAttempt.outcome != "authentication",
            ScheduledEmailAttempt.attempted_at >= start,
            ScheduledEmailAttempt.attempted_at <= now_utc,
        )
    ).all()
    timestamps = [timestamp.replace(tzinfo=timezone.utc) if timestamp.tzinfo is None else timestamp for timestamp in [*campaign_attempts, *individual_attempts]]
    return sorted(timestamps)


def sent_today(db, profile: Profile, campaign: Campaign | None, now_utc: datetime | None = None) -> int:
    """SMTP message attempts by this profile in the preceding rolling 24 hours.

    The historical name is retained for API compatibility; it is intentionally
    not a calendar-day count. Failed and retried message submissions consume
    capacity; connection/authentication failures before submission do not.
    """
    current = now_utc or datetime.now(timezone.utc)
    return len(_profile_attempt_timestamps(db, profile, current))


def next_profile_send_slot(db, profile: Profile, campaign: Campaign | None, now_utc: datetime | None = None) -> datetime | None:
    """Return when the next rolling-cap slot opens, or None when one is free."""
    current = now_utc or datetime.now(timezone.utc)
    attempts = _profile_attempt_timestamps(db, profile, current)
    if len(attempts) < profile.daily_cap:
        return None
    oldest = attempts[0]
    if oldest.tzinfo is None:
        oldest = oldest.replace(tzinfo=timezone.utc)
    return oldest + timedelta(hours=24)


def _scheduled_email_is_suppressed(db, email: ScheduledEmail) -> bool:
    markers = (
        db.scalar(select(UnsubscribeEvent.source_event_id).where(func.lower(UnsubscribeEvent.email) == email.normalized_email).limit(1)),
        db.scalar(select(ManualSuppressionEvent.id).where(ManualSuppressionEvent.email == email.normalized_email).limit(1)),
        db.scalar(select(Recipient.id).where(Recipient.normalized_email == email.normalized_email, Recipient.suppressed).limit(1)),
    )
    return any(marker is not None for marker in markers)


def process_scheduled_email(email_id: str) -> None:
    with SessionLocal() as db:
        email = db.get(ScheduledEmail, email_id)
        if not email or email.status not in {"scheduled", "retry"}:
            return
        profile = db.get(Profile, email.profile_id)
        if not profile:
            email.status = "failed"
            email.last_error = "sender profile not found"
            db.commit()
            return
        if _scheduled_email_is_suppressed(db, email):
            email.status = "suppressed"
            email.last_error = "recipient is on the suppression list"
            db.commit()
            return
        next_slot = next_profile_send_slot(db, profile, None)
        if next_slot:
            email.status = "scheduled"
            email.scheduled_at = next_slot
            email.last_error = "delayed by the sender profile rolling 24-hour cap"
            db.commit()
            return

        email.status = "sending"
        email.last_error = None
        db.commit()
        try:
            client = connect(profile, password=get_secret(profile.id, "password"), access_token=get_secret(profile.id, "access_token"))
        except Exception as exc:
            kind, code = ("authentication", None) if isinstance(exc, AuthenticationFailure) else classify_smtp_error(exc)
            attempt_no = (db.scalar(select(func.count()).select_from(ScheduledEmailAttempt).where(ScheduledEmailAttempt.scheduled_email_id == email.id)) or 0) + 1
            retry_at = datetime.now(timezone.utc) + timedelta(seconds=RETRY_DELAYS[attempt_no - 1]) if kind == "transient" and attempt_no <= len(RETRY_DELAYS) else None
            email.status = "retry" if retry_at else "failed"
            if retry_at:
                email.scheduled_at = retry_at
            email.last_error = str(exc)[:1000]
            db.add(ScheduledEmailAttempt(scheduled_email_id=email.id, outcome=kind, smtp_code=code, detail=email.last_error, retry_at=retry_at))
            db.commit()
            return
        try:
            rendered = render_message(email.subject, email.body, email.body_mode, {})
            message = build_individual_message(profile, email.recipient_email, rendered)
            send(client, message)
            email.status = "sent"
            email.sent_at = datetime.now(timezone.utc)
            email.message_id = message["Message-ID"]
            db.add(ScheduledEmailAttempt(scheduled_email_id=email.id, outcome="sent"))
        except Exception as exc:
            kind, code = classify_smtp_error(exc)
            attempt_no = (db.scalar(select(func.count()).select_from(ScheduledEmailAttempt).where(ScheduledEmailAttempt.scheduled_email_id == email.id)) or 0) + 1
            retry_at = datetime.now(timezone.utc) + timedelta(seconds=RETRY_DELAYS[attempt_no - 1]) if kind == "transient" and attempt_no <= len(RETRY_DELAYS) else None
            email.status = "retry" if retry_at else "failed"
            if retry_at:
                email.scheduled_at = retry_at
            email.last_error = str(exc)[:1000]
            db.add(ScheduledEmailAttempt(scheduled_email_id=email.id, outcome=kind, smtp_code=code, detail=email.last_error, retry_at=retry_at))
        finally:
            try:
                client.quit()
            except Exception:
                pass
        db.commit()


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
                next_slot = next_profile_send_slot(db, profile, campaign)
                if next_slot:
                    campaign.state = CampaignState.scheduled
                    campaign.scheduled_at = next_dispatch_start(campaign, profile, next_slot)
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
    with SessionLocal() as db:
        scheduled_emails = db.scalars(
            select(ScheduledEmail).where(
                ScheduledEmail.status.in_(["scheduled", "retry"]),
                ScheduledEmail.scheduled_at <= now,
            ).order_by(ScheduledEmail.scheduled_at.asc())
        ).all()
        individual_due = [email.id for email in scheduled_emails]
    for email_id in individual_due:
        process_scheduled_email(email_id)


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
