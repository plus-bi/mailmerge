from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import mailmerge.api as api_module
from mailmerge import worker
from mailmerge.api import ScheduledEmailIn, ScheduledEmailOut, cancel_scheduled_email, create_scheduled_emails, preflight_scheduled_emails, preview_scheduled_emails, update_scheduled_email
from mailmerge.db import Base
from mailmerge.models import ManualSuppressionEvent, Profile, ScheduledEmail, ScheduledEmailAttempt


@pytest.fixture
def test_db_session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'scheduled.sqlite3'}")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        yield session
    finally:
        session.close()


def _profile(db, *, daily_cap=250):
    profile = Profile(
        name="Individual sender",
        from_name="Sender",
        from_address="sender@example.com",
        smtp_host="localhost",
        smtp_port=1025,
        security="none",
        daily_cap=daily_cap,
    )
    db.add(profile)
    db.commit()
    return profile


def _payload(profile, *, email="person@example.com"):
    return {
        "email": email,
        "subject": "Checking in",
        "body": "Hi,\n\nJust following up.",
        "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        "profile_id": profile.id,
        "body_mode": "markdown",
    }


def _smtp_client():
    smtp = MagicMock()
    smtp.noop.return_value = (250, b"OK")
    return smtp


def test_json_payload_preview_preflight_schedule_edit_and_cancel(test_db_session, monkeypatch):
    profile = _profile(test_db_session)
    payload = _payload(profile)
    smtp = _smtp_client()
    monkeypatch.setattr(api_module, "connect", lambda *args, **kwargs: smtp)
    monkeypatch.setattr(api_module, "get_secret", lambda *args: None)

    input_data = ScheduledEmailIn(**payload)
    preview = preview_scheduled_emails(input_data, test_db_session)
    assert preview["previews"][0]["subject"] == "Checking in"
    assert "Just following up" in preview["previews"][0]["html"]

    preflight = preflight_scheduled_emails(input_data, test_db_session)
    assert preflight["ok"] is True

    scheduled = create_scheduled_emails(input_data, test_db_session)[0]
    output = ScheduledEmailOut.model_validate(scheduled).model_dump(mode="json", by_alias=True)
    assert output["email"] == "person@example.com"
    assert output["status"] == "scheduled"

    payload["subject"] = "Updated subject"
    payload["scheduled_at"] = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    updated = update_scheduled_email(scheduled.id, ScheduledEmailIn(**payload), test_db_session)
    assert updated.subject == "Updated subject"

    cancelled = cancel_scheduled_email(scheduled.id, test_db_session)
    assert cancelled.status == "cancelled"


def test_scheduled_email_rejects_suppressed_address(test_db_session):
    profile = _profile(test_db_session)
    test_db_session.add(ManualSuppressionEvent(email="person@example.com", reason="Requested no contact"))
    test_db_session.commit()

    with pytest.raises(HTTPException) as raised:
        preview_scheduled_emails(ScheduledEmailIn(**_payload(profile)), test_db_session)

    assert raised.value.status_code == 409
    assert "suppression list" in raised.value.detail


def test_worker_sends_due_individual_email(test_db_session, monkeypatch):
    profile = _profile(test_db_session)
    email = ScheduledEmail(
        profile_id=profile.id,
        recipient_email="person@example.com",
        normalized_email="person@example.com",
        subject="Checking in",
        body="Hello from the individual scheduler.",
        scheduled_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        status="scheduled",
    )
    test_db_session.add(email)
    test_db_session.commit()
    factory = sessionmaker(bind=test_db_session.get_bind(), expire_on_commit=False)
    monkeypatch.setattr(worker, "SessionLocal", factory)
    monkeypatch.setattr(worker, "connect", lambda *args, **kwargs: _smtp_client())
    monkeypatch.setattr(worker, "get_secret", lambda *args: None)
    sent_messages = []
    monkeypatch.setattr(worker, "send", lambda _client, message: sent_messages.append(message))

    worker.process_scheduled_email(email.id)

    test_db_session.expire_all()
    stored = test_db_session.get(ScheduledEmail, email.id)
    assert stored.status == "sent"
    assert stored.sent_at is not None
    assert sent_messages[0]["To"] == "person@example.com"
    assert test_db_session.query(ScheduledEmailAttempt).filter_by(scheduled_email_id=email.id, outcome="sent").count() == 1


def test_individual_sends_count_toward_profile_rolling_cap(test_db_session):
    profile = _profile(test_db_session, daily_cap=1)
    email = ScheduledEmail(
        profile_id=profile.id,
        recipient_email="person@example.com",
        normalized_email="person@example.com",
        subject="Sent",
        body="Sent",
        scheduled_at=datetime.now(timezone.utc),
        status="sent",
    )
    test_db_session.add(email)
    test_db_session.flush()
    now = datetime.now(timezone.utc)
    test_db_session.add(ScheduledEmailAttempt(scheduled_email_id=email.id, outcome="sent", attempted_at=now - timedelta(hours=23)))
    test_db_session.commit()

    assert worker.sent_today(test_db_session, profile, None, now) == 1
    assert worker.next_profile_send_slot(test_db_session, profile, None, now) == now + timedelta(hours=1)
