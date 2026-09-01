import json
import sqlite3
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from mailmerge.api import (
    CampaignIn,
    DuplicateCampaignIn,
    ProfileConnectionTestIn,
    TestEmailIn as ApiTestEmailIn,
    campaign_statuses,
    duplicate_campaign,
    list_suppressions,
    preflight,
    preview_recipient,
    router,
    send_test_email,
    test_profile_connection as run_profile_connection_test,
    trigger_global_suppression_sync,
    update_campaign,
)
from mailmerge.config import settings
from mailmerge.db import Base, get_db
from mailmerge.models import Campaign, Profile, Recipient, CampaignState
from mailmerge.suppression import sync_suppressions
from fastapi import FastAPI


@pytest.fixture
def test_db_session(tmp_path):
    db_file = tmp_path / "test.sqlite3"
    engine = create_engine(f"sqlite:///{db_file}")
    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def client(test_db_session):
    app = FastAPI()
    app.include_router(router)

    def override_get_db():
        try:
            yield test_db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app)


def test_api_json_recipient_import_and_preflight(client, test_db_session):
    # 1. Create Profile
    profile_res = client.post(
        "/api/v1/profiles",
        json={"name": "Test Profile", "smtp_host": "localhost", "smtp_port": 1025, "security": "none"},
    )
    assert profile_res.status_code == 200
    profile_id = profile_res.json()["id"]

    # 2. Create Campaign
    campaign_res = client.post(
        "/api/v1/campaigns",
        json={
            "name": "Beta Launch",
            "profile_id": profile_id,
            "purpose": "operational",
            "from_name": "Team",
            "from_address": "team@example.com",
            "subject_template": "Welcome {{ first_name }} to {{ tier }}",
            "body_template": "Hi {{ first_name }},\n\nYour tier is **{{ tier }}**.",
            "body_mode": "markdown",
        },
    )
    assert campaign_res.status_code == 200
    campaign_id = campaign_res.json()["id"]

    # 3. Import JSON Recipients with one missing 'tier' variable
    recipients_data = [
        {"email": "alice@example.com", "first_name": "Alice", "tier": "Gold"},
        {"email": "bob@example.com", "first_name": "Bob"},  # missing tier
        {"email": "carol@example.com", "first_name": "Carol", "tier": "Silver"},
    ]
    import_res = client.post(f"/api/v1/campaigns/{campaign_id}/recipients", json=recipients_data)
    assert import_res.status_code == 200
    import_info = import_res.json()
    assert import_info["imported"] == 3
    assert import_info["valid"] == 2
    assert len(import_info["errors"]) == 1

    # 4. Get Recipients list
    recipients_list_res = client.get(f"/api/v1/campaigns/{campaign_id}/recipients")
    assert recipients_list_res.status_code == 200
    recipients = recipients_list_res.json()
    assert len(recipients) == 3
    alice = next(r for r in recipients if r["email"] == "alice@example.com")
    bob = next(r for r in recipients if r["email"] == "bob@example.com")
    assert alice["valid"] is True
    assert bob["valid"] is False
    assert "missing required template variable(s): tier" in bob["validation_error"]

    # 5. Preview specific recipient
    preview_res = client.get(f"/api/v1/campaigns/{campaign_id}/preview/{alice['id']}")
    assert preview_res.status_code == 200
    preview_data = preview_res.json()
    assert preview_data["subject"] == "Welcome Alice to Gold"
    assert "<strong>Gold</strong>" in preview_data["html"]
    assert "Hi Alice" in preview_data["text"]

    # 6. Run Preflight
    preflight_res = client.post(f"/api/v1/campaigns/{campaign_id}/preflight")
    assert preflight_res.status_code == 200
    pf = preflight_res.json()
    assert pf["ok"] is True
    assert len(pf["previews"]) == 2  # Alice and Carol
    assert pf["excluded"] == 1  # Bob was excluded due to validation error

    # 7. Delete Campaign
    del_res = client.delete(f"/api/v1/campaigns/{campaign_id}")
    assert del_res.status_code == 200
    assert del_res.json()["ok"] is True

    # 8. Verify Campaign and its recipients are deleted
    get_res = client.get(f"/api/v1/campaigns/{campaign_id}")
    assert get_res.status_code == 404


def test_campaign_statuses_only_include_launched_campaigns(test_db_session):
    draft = Campaign(name="Draft", state=CampaignState.draft)
    launched = Campaign(
        name="Launched",
        state=CampaignState.failed,
        scheduled_at=datetime.now(timezone.utc),
    )
    test_db_session.add_all([draft, launched])
    test_db_session.flush()
    test_db_session.add_all(
        [
            Recipient(
                campaign_id=launched.id,
                email="sent@example.com",
                normalized_email="sent@example.com",
                status="sent",
            ),
            Recipient(
                campaign_id=launched.id,
                email="failed@example.com",
                normalized_email="failed@example.com",
                status="failed",
            ),
        ]
    )
    test_db_session.commit()

    response = [item.model_dump(mode="json") for item in campaign_statuses(test_db_session)]

    assert response == [
        {
            "id": launched.id,
            "name": "Launched",
            "state": "failed",
            "scheduled_at": launched.scheduled_at.isoformat(),
            "counts": {"failed": 1, "sent": 1},
            "total": 2,
        }
    ]


def test_cancelled_campaign_can_be_edited(test_db_session):
    campaign = Campaign(name="Cancelled", state=CampaignState.cancelled)
    test_db_session.add(campaign)
    test_db_session.commit()

    updated = update_campaign(
        campaign.id,
        CampaignIn(name="Cancelled, updated", subject_template="Updated subject"),
        test_db_session,
    )

    assert updated.name == "Cancelled, updated"
    assert updated.subject_template == "Updated subject"
    assert updated.state == CampaignState.cancelled


def test_duplicate_campaign_copies_settings_into_clean_draft(test_db_session):
    source = Campaign(
        name="Original",
        purpose="marketing",
        state=CampaignState.cancelled,
        scheduled_at=datetime.now(timezone.utc),
        from_name="Sender",
        from_address="sender@example.com",
        subject_template="Hello {{ first_name }}",
        body_template="Body {{ unsubscribe_url }}",
        consent_acknowledged=True,
        list_unsubscribe_enabled=True,
        unsubscribe_base_url="https://unsub.plus.bi",
    )
    test_db_session.add(source)
    test_db_session.flush()
    test_db_session.add(
        Recipient(
            campaign_id=source.id,
            email="person@example.com",
            normalized_email="person@example.com",
        )
    )
    test_db_session.commit()

    duplicate = duplicate_campaign(
        source.id,
        DuplicateCampaignIn(name="Follow-up campaign"),
        test_db_session,
    )

    assert duplicate.id != source.id
    assert duplicate.name == "Follow-up campaign"
    assert duplicate.state == CampaignState.draft
    assert duplicate.scheduled_at is None
    assert duplicate.purpose == source.purpose
    assert duplicate.subject_template == source.subject_template
    assert duplicate.body_template == source.body_template
    assert duplicate.list_unsubscribe_enabled is True
    assert duplicate.recipients == []


def test_send_test_email_injects_unsubscribe_url(test_db_session, monkeypatch):
    profile = Profile(name="Test SMTP", smtp_host="localhost", smtp_port=1025, security="none")
    test_db_session.add(profile)
    test_db_session.flush()
    campaign = Campaign(
        name="Unsubscribe test",
        profile_id=profile.id,
        from_address="sender@example.com",
        subject_template="Hello {{ first_name }}",
        body_template="Hi {{ first_name }}. Unsubscribe: {{ unsubscribe_url }}",
        list_unsubscribe_enabled=True,
        unsubscribe_base_url="https://unsub.plus.bi",
    )
    test_db_session.add(campaign)
    test_db_session.flush()
    recipient = Recipient(
        campaign_id=campaign.id,
        email="sample@example.com",
        normalized_email="sample@example.com",
        values={"first_name": "Sample"},
    )
    test_db_session.add(recipient)
    test_db_session.commit()
    monkeypatch.setenv("UNSUBSCRIBE_SIGNING_SECRET", "test-secret")
    smtp_client = MagicMock()
    sent_messages = []

    with (
        patch("mailmerge.api.get_secret", return_value=None),
        patch("mailmerge.api.connect", return_value=smtp_client),
        patch("mailmerge.api.send", lambda _client, message: sent_messages.append(message)),
    ):
        result = send_test_email(
            campaign.id,
            ApiTestEmailIn(recipient_email="tester@example.com", sample_recipient_id=recipient.id),
            test_db_session,
        )

    assert result["ok"] is True
    plain_body = sent_messages[0].get_body(preferencelist=("plain",)).get_content()
    assert "https://unsub.plus.bi/u/" in plain_body


def test_preview_suppresses_unsubscribe_line_when_disabled(test_db_session):
    campaign = Campaign(
        name="No unsubscribe preview",
        purpose="operational",
        from_address="sender@example.com",
        subject_template="Hello {{ first_name }}",
        body_template=(
            "Hi {{ first_name }}.\n\n"
            "If you prefer not to receive these emails, you can "
            "[unsubscribe here]({{ unsubscribe_url }}).\n\nRegards"
        ),
        body_mode="markdown",
        list_unsubscribe_enabled=False,
    )
    test_db_session.add(campaign)
    test_db_session.flush()
    recipient = Recipient(
        campaign_id=campaign.id,
        email="reader@example.com",
        normalized_email="reader@example.com",
        values={"first_name": "Reader"},
    )
    test_db_session.add(recipient)
    test_db_session.commit()

    preview = preview_recipient(campaign.id, recipient.id, test_db_session)

    assert "unsubscribe_url" not in preview["text"]
    assert "If you prefer" not in preview["text"]
    assert "Hi Reader." in preview["text"]
    assert "Regards" in preview["text"]


def test_send_test_email_suppresses_unsubscribe_line_when_disabled(test_db_session):
    profile = Profile(name="Test SMTP", smtp_host="localhost", smtp_port=1025, security="none")
    test_db_session.add(profile)
    test_db_session.flush()
    campaign = Campaign(
        name="No unsubscribe test email",
        profile_id=profile.id,
        from_address="sender@example.com",
        subject_template="Hello",
        body_template="Body\n\nUnsubscribe: {{ unsubscribe_url }}\n\nRegards",
        list_unsubscribe_enabled=False,
    )
    test_db_session.add(campaign)
    test_db_session.commit()
    smtp_client = MagicMock()
    sent_messages = []

    with (
        patch("mailmerge.api.get_secret", return_value=None),
        patch("mailmerge.api.connect", return_value=smtp_client),
        patch("mailmerge.api.send", lambda _client, message: sent_messages.append(message)),
    ):
        result = send_test_email(
            campaign.id,
            ApiTestEmailIn(recipient_email="tester@example.com"),
            test_db_session,
        )

    assert result["ok"] is True
    message = sent_messages[0]
    assert "unsubscribe_url" not in message.get_body(preferencelist=("plain",)).get_content()
    assert "Unsubscribe:" not in message.get_body(preferencelist=("plain",)).get_content()
    assert "Regards" in message.get_body(preferencelist=("plain",)).get_content()
    assert "List-Unsubscribe" not in message


def test_profile_manager_import_edit_and_save_toml(client, tmp_path, monkeypatch):
    profile_path = tmp_path / "profiles.toml"
    monkeypatch.setattr(settings, "profile_config", profile_path)
    content = '''
[[profiles]]
name = "Imported SMTP"
smtp_host = "smtp.example.com"
smtp_port = 587
security = "starttls"
daily_cap = 75
'''

    imported = client.post("/api/v1/profile-config", json={"content": content})
    assert imported.status_code == 200
    profile = imported.json()[0]
    assert profile_path.read_text() == content

    profile["daily_cap"] = 120
    updated = client.put(f"/api/v1/profiles/{profile['id']}", json=profile)
    assert updated.status_code == 200
    assert updated.json()["daily_cap"] == 120

    saved = client.put("/api/v1/profile-config")
    assert saved.status_code == 200
    assert "daily_cap = 120" in profile_path.read_text()
    exported = client.get("/api/v1/profile-config")
    assert exported.status_code == 200
    assert exported.json()["filename"] == "profiles.toml"


def test_profile_connection_uses_current_settings_and_stored_secret(test_db_session):
    profile = Profile(
        name="SMTP connection",
        smtp_host="smtp.example.com",
        smtp_port=587,
        security="starttls",
        username="sender@example.com",
    )
    test_db_session.add(profile)
    test_db_session.commit()
    smtp_client = MagicMock()
    smtp_client.noop.return_value = (250, b"OK")

    with (
        patch("mailmerge.api.get_secret", side_effect=lambda _id, kind: "stored-password" if kind == "password" else None),
        patch("mailmerge.api.connect", return_value=smtp_client) as connect_mock,
    ):
        result = run_profile_connection_test(
            ProfileConnectionTestIn(
                profile_id=profile.id,
                name=profile.name,
                smtp_host=profile.smtp_host,
                smtp_port=profile.smtp_port,
                security="starttls",
                username=profile.username,
            ),
            test_db_session,
        )

    assert result["ok"] is True
    assert result["message"] == "SMTP connection and authentication succeeded."
    assert result["server"] == "smtp.example.com"
    assert connect_mock.call_args.kwargs["password"] == "stored-password"
    smtp_client.noop.assert_called_once_with()
    smtp_client.quit.assert_called_once_with()



def test_send_test_email(client, monkeypatch):
    # 1. Create Profile and Campaign
    profile_res = client.post(
        "/api/v1/profiles",
        json={"name": "SMTP Test", "smtp_host": "localhost", "smtp_port": 1025, "security": "none"},
    )
    profile_id = profile_res.json()["id"]

    campaign_res = client.post(
        "/api/v1/campaigns",
        json={
            "name": "Test Run",
            "profile_id": profile_id,
            "purpose": "operational",
            "from_name": "Test Sender",
            "from_address": "sender@example.com",
            "subject_template": "Hello {{ name }}",
            "body_template": "Your code is {{ code }}.",
            "body_mode": "markdown",
        },
    )
    campaign_id = campaign_res.json()["id"]

    client.post(
        f"/api/v1/campaigns/{campaign_id}/recipients",
        json=[{"email": "sample@example.com", "name": "Sample Person", "code": "12345"}],
    )

    sent_messages = []
    mock_smtp = MagicMock()
    mock_connect = MagicMock(return_value=mock_smtp)

    with (
        patch("mailmerge.api.get_secret", return_value=None),
        patch("mailmerge.api.connect", mock_connect),
        patch("mailmerge.api.send", lambda cl, msg: sent_messages.append(msg)),
    ):
        res = client.post(
            f"/api/v1/campaigns/{campaign_id}/test-email",
            json={"recipient_email": "tester@example.com"},
        )
        assert res.status_code == 200
        assert res.json()["ok"] is True
        assert res.json()["recipient_email"] == "tester@example.com"
        assert len(sent_messages) == 1
        assert sent_messages[0]["Subject"] == "[TEST] Hello Sample Person"
        assert sent_messages[0]["To"] == "tester@example.com"


def test_suppression_sync_from_sqlite_db(test_db_session, tmp_path, monkeypatch):
    # Setup unsubscribe SQLite DB
    unsub_db_file = tmp_path / "unsub_events.db"
    with sqlite3.connect(unsub_db_file) as conn:
        conn.execute("""CREATE TABLE events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_id TEXT NOT NULL,
            recipient_id TEXT NOT NULL,
            created_at INTEGER NOT NULL
        )""")
        conn.execute("INSERT INTO events (campaign_id, recipient_id, created_at) VALUES ('c1', 'r1', 1700000000)")

    monkeypatch.setenv("UNSUBSCRIBE_DB", str(unsub_db_file))

    # Add a campaign and recipient
    campaign = Campaign(id="c1", name="Camp 1", purpose="operational", suppression_synced=False)
    recipient = Recipient(id="r1", campaign_id="c1", email="unsub@example.com", normalized_email="unsub@example.com")
    # Another recipient with same email in another campaign
    recipient2 = Recipient(id="r2", campaign_id="c2", email="unsub@example.com", normalized_email="unsub@example.com")
    test_db_session.add_all([campaign, recipient, recipient2])
    test_db_session.commit()

    count = sync_suppressions(test_db_session)
    assert count == 1
    assert recipient.suppressed is True
    assert recipient2.suppressed is True
    assert campaign.suppression_synced is True
    suppression_list = list_suppressions(test_db_session)
    events = suppression_list["events"]
    assert len(events) == 1
    assert events[0].source_event_id == 1
    assert events[0].email == "unsub@example.com"
    assert events[0].campaign == "c1"
    assert events[0].unsubscribed_at == datetime.fromtimestamp(1700000000, timezone.utc).replace(tzinfo=None)
    assert suppression_list["last_synced_at"] is not None

    result = trigger_global_suppression_sync(test_db_session)
    assert result == {"ok": True, "synced_events": 0, "total": 1}


def test_generate_unsubscribe_token_endpoint(client, test_db_session, monkeypatch):
    monkeypatch.setenv("UNSUBSCRIBE_SIGNING_SECRET", "test-secret-123")
    campaign = Campaign(id="camp-xyz", name="Camp XYZ", purpose="marketing")
    test_db_session.add(campaign)
    test_db_session.commit()

    res = client.post(
        "/api/v1/campaigns/camp-xyz/generate-unsubscribe-token",
        json={"recipient_id": "all", "base_url": "https://unsub.example.com"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["campaign_id"] == "Camp XYZ"
    assert data["recipient_id"] == "all"
    assert data["token"]
    assert data["unsubscribe_url"].startswith("https://unsub.example.com/u/")
    assert data["token"] in data["unsubscribe_url"]

    # Verify token with unsubscribe service
    from unsubscribe_service.main import verify_token
    payload = verify_token(data["token"], secret="test-secret-123")
    assert payload["c"] == "Camp XYZ"
    assert payload["r"] == "all"


def test_get_unsubscribe_config(client, monkeypatch):
    monkeypatch.setenv("UNSUBSCRIBE_SIGNING_SECRET", "test-secret-123")
    monkeypatch.setenv("DOMAIN", "mail.example.com")
    res = client.get("/api/v1/unsubscribe-config")
    assert res.status_code == 200
    data = res.json()
    assert data["signing_secret_configured"] is True
    assert data["domain"] == "mail.example.com"
    assert data["default_base_url"] == "https://unsub.plus.bi"


def test_suppression_sync_with_email_recipient_id(test_db_session, tmp_path, monkeypatch):
    unsub_db_file = tmp_path / "unsub_events.db"
    with sqlite3.connect(unsub_db_file) as conn:
        conn.execute("""CREATE TABLE events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_id TEXT NOT NULL,
            recipient_id TEXT NOT NULL,
            created_at INTEGER NOT NULL
        )""")
        conn.execute("INSERT INTO events (campaign_id, recipient_id, created_at) VALUES ('Camp 1', 'target@example.com', 1700000000)")

    monkeypatch.setenv("UNSUBSCRIBE_DB", str(unsub_db_file))

    campaign = Campaign(id="c1", name="Camp 1", purpose="operational", suppression_synced=False)
    recipient = Recipient(id="r1", campaign_id="c1", email="Target@Example.com", normalized_email="target@example.com")
    recipient2 = Recipient(id="r2", campaign_id="c2", email="target@example.com", normalized_email="target@example.com")
    test_db_session.add_all([campaign, recipient, recipient2])
    test_db_session.commit()

    count = sync_suppressions(test_db_session)
    assert count == 1
    assert recipient.suppressed is True
    assert recipient2.suppressed is True
