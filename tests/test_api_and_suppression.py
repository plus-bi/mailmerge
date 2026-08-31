import json
import sqlite3
from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from mailmerge.api import router, preflight
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
