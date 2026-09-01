from datetime import datetime, timezone
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
        body_template="Hi {{ name }}",
        list_unsubscribe_enabled=True,
    )
    test_db_session.add(campaign)
    test_db_session.flush()
    return campaign


def test_worker_records_delivery_failure_and_marks_campaign_failed(test_db_session, monkeypatch):
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
    assert test_db_session.get(Campaign, campaign.id).state == CampaignState.failed
    assert test_db_session.get(Recipient, recipient.id).status == "failed"
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
    assert test_db_session.get(Recipient, valid.id).status == "sent"
    assert test_db_session.get(Recipient, excluded.id).status == "pending"
