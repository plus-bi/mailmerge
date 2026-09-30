from __future__ import annotations

import imaplib
import json
import ssl
from datetime import datetime, timezone
from email import policy
from email.message import Message
from email.parser import BytesParser
from email.utils import getaddresses, parsedate_to_datetime
from typing import Any

from .models import Profile


def _text_body(message: Message) -> str:
    if message.is_multipart():
        for part in message.walk():
            if part.get_content_type() != "text/plain" or part.get_content_disposition() == "attachment":
                continue
            try:
                return str(part.get_content())
            except (LookupError, UnicodeError):
                payload = part.get_payload(decode=True) or b""
                return payload.decode("utf-8", errors="replace")
        return ""
    try:
        return str(message.get_content())
    except (LookupError, UnicodeError):
        payload = message.get_payload(decode=True) or b""
        return payload.decode("utf-8", errors="replace")


def _received_at(message: Message) -> datetime | None:
    value = message.get("Date")
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _message_row(profile: Profile, uid: str, raw: bytes) -> dict[str, Any]:
    message = BytesParser(policy=policy.default).parsebytes(raw)
    recipients = [address for _name, address in getaddresses(message.get_all("to", [])) if address]
    sender = getaddresses(message.get_all("from", []))
    return {
        "uid": uid,
        "profile_id": profile.id,
        "message_id": str(message.get("Message-ID") or ""),
        "from_address": sender[0][1] if sender else "",
        "to_addresses": recipients,
        "subject": str(message.get("Subject") or "(no subject)"),
        "received_at": _received_at(message),
        "text_body": _text_body(message)[:200_000],
        "headers_json": json.dumps({key: str(value) for key, value in message.items()}),
        "message_json": message.as_string()[:500_000],
        "content_type": message.get_content_type(),
    }


def read_inbox(
    profile: Profile,
    *,
    password: str | None,
    access_token: str | None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    if not profile.imap_host or not profile.imap_port or not profile.imap_security:
        raise RuntimeError(f"profile {profile.name!r} has no IMAP connection configured")

    context = ssl.create_default_context()
    if not profile.verify_tls:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE

    client: imaplib.IMAP4 | None = None
    try:
        if profile.imap_security == "tls":
            client = imaplib.IMAP4_SSL(profile.imap_host, profile.imap_port, ssl_context=context, timeout=15)
        else:
            client = imaplib.IMAP4(profile.imap_host, profile.imap_port, timeout=15)
            if profile.imap_security == "starttls":
                client.starttls(ssl_context=context)

        if profile.auth_type == "xoauth2":
            if not access_token:
                raise RuntimeError(f"profile {profile.name!r} has no stored access token")
            auth = f"user={profile.username or ''}\x01auth=Bearer {access_token}\x01\x01"
            client.authenticate("XOAUTH2", lambda _challenge: auth.encode())
        else:
            if not password:
                raise RuntimeError(f"profile {profile.name!r} has no stored password")
            client.login(profile.username or "", password)

        status, _ = client.select("INBOX", readonly=True)
        if status != "OK":
            raise RuntimeError(f"profile {profile.name!r} inbox could not be opened")
        status, search_data = client.uid("search", None, "ALL")
        if status != "OK" or not search_data:
            raise RuntimeError(f"profile {profile.name!r} inbox could not be searched")

        uids = search_data[0].split()[-limit:]
        rows: list[dict[str, Any]] = []
        for raw_uid in reversed(uids):
            uid = raw_uid.decode("ascii", errors="replace")
            status, fetched = client.uid("fetch", uid, "(BODY.PEEK[])")
            if status != "OK" or not fetched:
                continue
            raw_message = next(
                (item[1] for item in fetched if isinstance(item, tuple) and isinstance(item[1], bytes)),
                None,
            )
            if raw_message is not None:
                rows.append(_message_row(profile, uid, raw_message))
        return rows
    except (imaplib.IMAP4.error, OSError, ssl.SSLError) as exc:
        raise RuntimeError(f"could not read inbox for profile {profile.name!r}: {exc}") from exc
    finally:
        if client is not None:
            try:
                client.logout()
            except (imaplib.IMAP4.error, OSError):
                pass
