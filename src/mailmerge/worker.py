from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from .config import settings
from .db import SessionLocal, init_db
from .messages import build_message
from .models import AuditLog, Campaign, CampaignState, DeliveryAttempt, Profile, Recipient
from .profile_config import load_profiles
from .rendering import render_message
from .secrets import get_secret
from .smtp import AuthenticationFailure, classify_smtp_error, connect, send

RETRY_DELAYS = (60, 300, 900)


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
            recipients = db.scalars(select(Recipient).where(Recipient.campaign_id == campaign.id, Recipient.included,
                                   Recipient.valid, ~Recipient.suppressed, Recipient.status.in_(["pending", "retry"]))).all()
            for recipient in recipients:
                db.refresh(campaign)
                if campaign.state != CampaignState.sending:
                    break
                attempts = db.scalar(select(DeliveryAttempt).where(DeliveryAttempt.recipient_id == recipient.id).order_by(DeliveryAttempt.id.desc()))
                if attempts and attempts.retry_at and attempts.retry_at > datetime.now(timezone.utc):
                    continue
                try:
                    values = dict(recipient.values)
                    values.setdefault("email", recipient.email)
                    rendered = render_message(campaign.subject_template, campaign.body_template, campaign.body_mode, values)
                    message = build_message(campaign, recipient.email, rendered)
                    send(client, message)
                    recipient.status = "sent"
                    recipient.message_id = message["Message-ID"]
                    recipient.sent_at = datetime.now(timezone.utc)
                    db.add(DeliveryAttempt(recipient_id=recipient.id, outcome="sent"))
                except Exception as exc:  # SMTP implementations expose multiple exception subclasses
                    kind, code = classify_smtp_error(exc)
                    attempt_no = len(db.scalars(select(DeliveryAttempt).where(DeliveryAttempt.recipient_id == recipient.id)).all()) + 1
                    if kind == "transient" and attempt_no <= len(RETRY_DELAYS):
                        recipient.status = "retry"
                        retry_at = datetime.now(timezone.utc) + timedelta(seconds=RETRY_DELAYS[attempt_no - 1])
                    else:
                        recipient.status = "failed"
                        retry_at = None
                    db.add(DeliveryAttempt(recipient_id=recipient.id, outcome=kind, smtp_code=code,
                                           detail=str(exc)[:1000], retry_at=retry_at))
                db.commit()
                if profile.delay_seconds:
                    time.sleep(profile.delay_seconds)
        finally:
            try:
                client.quit()
            except Exception:
                pass
        pending = db.scalar(select(Recipient).where(Recipient.campaign_id == campaign.id,
                            Recipient.status.in_(["pending", "retry"])).limit(1))
        if campaign.state == CampaignState.sending and not pending:
            campaign.state = CampaignState.completed
            db.add(AuditLog(campaign_id=campaign.id, action="completed"))
            db.commit()


def tick() -> None:
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
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
    if settings.profile_config:
        with SessionLocal() as db:
            load_profiles(settings.profile_config, db)
    while True:
        tick()
        time.sleep(settings.worker_poll_seconds)


if __name__ == "__main__":
    run()
