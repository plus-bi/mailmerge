from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import mailmerge.imap_inbox as inbox_module
from mailmerge.bounce_import import find_new_imap_bounces
from mailmerge.db import Base
from mailmerge.imap_inbox import read_inbox
from mailmerge.models import Campaign, CampaignState, Profile, Recipient


class FakeImap:
    def __init__(self, *args, **kwargs):
        self.calls = []

    def login(self, username, password):
        self.calls.append(("login", username, password))

    def select(self, mailbox, readonly=False):
        self.calls.append(("select", mailbox, readonly))
        return "OK", [b"1"]

    def uid(self, command, *args):
        self.calls.append(("uid", command, *args))
        if command == "search":
            return "OK", [b"41"]
        return "OK", [(b"41 (BODY[] {1})", MESSAGE)]

    def logout(self):
        self.calls.append(("logout",))


MESSAGE = b"""From: Mail Delivery System <mailer-daemon@example.net>
To: haris.jabber@tum.de
Subject: Undelivered Mail Returned to Sender
Message-ID: <bounce-1@example.net>
Date: Tue, 22 Sep 2026 09:00:00 +0200
Content-Type: text/plain; charset=utf-8

Delivery failed.
Final-Recipient: rfc822; person@example.com
Diagnostic-Code: smtp; 550 mailbox unavailable
"""


def test_read_inbox_uses_readonly_peek_and_plain_text(monkeypatch):
    client = FakeImap()
    monkeypatch.setattr(inbox_module.imaplib, "IMAP4_SSL", lambda *args, **kwargs: client)
    profile = Profile(
        name="TUM",
        from_address="haris.jabber@tum.de",
        smtp_host="postout.lrz.de",
        smtp_port=587,
        security="starttls",
        username="go96gab",
        imap_host="xmail.mwn.de",
        imap_port=993,
        imap_security="tls",
    )

    messages = read_inbox(profile, password="stored-password", access_token=None)

    assert messages[0]["uid"] == "41"
    assert messages[0]["subject"] == "Undelivered Mail Returned to Sender"
    assert "person@example.com" in messages[0]["text_body"]
    assert ("select", "INBOX", True) in client.calls
    assert ("uid", "fetch", "41", "(BODY.PEEK[])") in client.calls


def test_imap_bounce_becomes_manual_review_candidate(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'imap.sqlite3'}")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        profile = Profile(name="TUM", smtp_host="postout.lrz.de", from_address="haris.jabber@tum.de")
        session.add(profile)
        session.flush()
        campaign = Campaign(name="Campaign", profile_id=profile.id, state=CampaignState.completed)
        session.add(campaign)
        session.flush()
        recipient = Recipient(
            campaign_id=campaign.id,
            email="person@example.com",
            normalized_email="person@example.com",
            values={"email": "person@example.com"},
        )
        session.add(recipient)
        session.commit()
        message = {
            "uid": "41",
            "message_id": "<bounce-1@example.net>",
            "subject": "Undelivered Mail Returned to Sender",
            "text_body": "Final-Recipient: rfc822; person@example.com",
            "headers_json": "{}",
            "content_type": "text/plain",
            "received_at": datetime(2026, 9, 22, 7, 0, tzinfo=timezone.utc),
        }

        matches = find_new_imap_bounces(session, profile, [message])

        assert len(matches) == 1
        assert matches[0].source == "IMAP bounce"
        assert matches[0].marker.startswith(f"imap:{profile.id}:41:")
        assert matches[0].recipients == [recipient]
    finally:
        session.close()
