from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from mailmerge import worker
from mailmerge.db import Base
from mailmerge.models import Campaign, CampaignState, DeliveryAttempt, Profile, Recipient


@pytest.fixture
def test_db_session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.sqlite3'}")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


def _worker_session(test_db_session, monkeypatch):
    factory = sessionmaker(bind=test_db_session.get_bind(), expire_on_commit=False)
    monkeypatch.setattr(worker, "SessionLocal", factory)


def _scheduled_campaign(test_db_session, *, name: str) -> Campaign:
    profile = Profile(name=f"{name} profile", smtp_host="localhost", smtp_port=1025, security="none")
    test_db_session.add(profile)
    test_db_session.flush()
    campaign = Campaign(
        name=name,
        profile_id=profile.id,
        state=CampaignState.scheduled,
        scheduled_at=datetime.now(timezone.utc),
        from_address="sender@example.com",
        subject_template="Hello {{ name }}",
        body_template="Hi **{{ name }}**",
        list_unsubscribe_enabled=True,
        working_hours_start=0,
        working_hours_end=23,
    )
    test_db_session.add(campaign)
    test_db_session.flush()
    return campaign


def test_worker_records_delivery_failure_and_completes_campaign(test_db_session, monkeypatch):
    campaign = _scheduled_campaign(test_db_session, name="Failed run")
    recipient = Recipient(
        campaign_id=campaign.id,
        email="person@example.com",
        normalized_email="person@example.com",
        values={"name": "Person"},
    )
    test_db_session.add(recipient)
    test_db_session.commit()
    _worker_session(test_db_session, monkeypatch)
    client = MagicMock()
    monkeypatch.setattr(worker, "connect", lambda *args, **kwargs: client)
    monkeypatch.setattr(worker, "get_secret", lambda *args: None)

    def fail_send(*args):
        raise RuntimeError("SMTP test failure")

    monkeypatch.setattr(worker, "send", fail_send)
    monkeypatch.delenv("UNSUBSCRIBE_SIGNING_SECRET", raising=False)

    worker.process_campaign(campaign.id)

    test_db_session.expire_all()
    assert test_db_session.get(Campaign, campaign.id).state == CampaignState.completed
    failed_recipient = test_db_session.get(Recipient, recipient.id)
    assert failed_recipient.status == "failed"
    attempt = test_db_session.query(DeliveryAttempt).filter_by(recipient_id=recipient.id).one()
    assert attempt.outcome == "permanent"
    assert attempt.detail == "SMTP test failure"


def test_worker_completes_when_all_sendable_recipients_are_sent(test_db_session, monkeypatch):
    campaign = _scheduled_campaign(test_db_session, name="Successful run")
    valid = Recipient(
        campaign_id=campaign.id,
        email="valid@example.com",
        normalized_email="valid@example.com",
        values={"name": "Valid"},
    )
    excluded = Recipient(
        campaign_id=campaign.id,
        email="excluded@example.com",
        normalized_email="excluded@example.com",
        values={},
        valid=False,
        included=False,
        validation_error="missing name",
    )
    test_db_session.add_all([valid, excluded])
    test_db_session.commit()
    _worker_session(test_db_session, monkeypatch)
    client = MagicMock()
    monkeypatch.setattr(worker, "connect", lambda *args, **kwargs: client)
    monkeypatch.setattr(worker, "get_secret", lambda *args: None)
    monkeypatch.setattr(worker, "send", lambda *args: None)

    worker.process_campaign(campaign.id)

    test_db_session.expire_all()
    assert test_db_session.get(Campaign, campaign.id).state == CampaignState.completed
    sent_recipient = test_db_session.get(Recipient, valid.id)
    assert sent_recipient.status == "sent"
    assert not hasattr(sent_recipient, "rendered_subject")
    assert not hasattr(sent_recipient, "rendered_markdown")
    assert test_db_session.get(Recipient, excluded.id).status == "pending"


def test_worker_handles_naive_sqlite_retry_timestamp(test_db_session, monkeypatch):
    campaign = _scheduled_campaign(test_db_session, name="Naive retry")
    recipient = Recipient(
        campaign_id=campaign.id,
        email="retry@example.com",
        normalized_email="retry@example.com",
        values={"name": "Retry"},
        status="retry",
    )
    test_db_session.add(recipient)
    test_db_session.flush()
    test_db_session.add(DeliveryAttempt(
        recipient_id=recipient.id,
        outcome="transient",
        retry_at=datetime.now() + timedelta(minutes=5),
    ))
    test_db_session.commit()
    _worker_session(test_db_session, monkeypatch)
    client = MagicMock()
    monkeypatch.setattr(worker, "connect", lambda *args, **kwargs: client)
    monkeypatch.setattr(worker, "get_secret", lambda *args: None)
    send = MagicMock()
    monkeypatch.setattr(worker, "send", send)

    worker.process_campaign(campaign.id)

    send.assert_not_called()
    test_db_session.expire_all()
    assert test_db_session.get(Campaign, campaign.id).state == CampaignState.sending


def test_profile_daily_cap_uses_a_rolling_24_hour_window(test_db_session):
    profile = Profile(name="Rolling cap", smtp_host="localhost", smtp_port=1025, security="none", daily_cap=2)
    test_db_session.add(profile)
    test_db_session.flush()
    campaign = Campaign(name="Rolling source", profile_id=profile.id)
    target = Campaign(name="Rolling target", profile_id=profile.id)
    test_db_session.add_all([campaign, target])
    test_db_session.flush()
    first = Recipient(campaign_id=campaign.id, email="one@example.com", normalized_email="one@example.com")
    second = Recipient(campaign_id=campaign.id, email="two@example.com", normalized_email="two@example.com")
    test_db_session.add_all([first, second])
    test_db_session.flush()
    now = datetime(2026, 9, 15, 20, 30, tzinfo=timezone.utc)
    test_db_session.add_all([
        DeliveryAttempt(recipient_id=first.id, outcome="sent", attempted_at=now - timedelta(hours=23, minutes=50)),
        DeliveryAttempt(recipient_id=second.id, outcome="permanent", smtp_code=550, attempted_at=now - timedelta(hours=23, minutes=40)),
    ])
    test_db_session.commit()

    assert worker.sent_today(test_db_session, profile, target, now) == 2
    assert worker.next_profile_send_slot(test_db_session, profile, target, now) == now + timedelta(minutes=10)
