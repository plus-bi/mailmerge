"""Import DSN bounces from the standalone Resend inbound monitor.

The monitor remains independent: this command consumes only its authenticated
read API and records the resulting suppression decision in Mailmerge.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .db import SessionLocal, init_db
from .models import AuditLog, BounceEvent, Campaign, CampaignState, DeliveryAttempt, Recipient

BOUNCE_SUBJECT = "Undelivered Mail Returned to Sender"
EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)


def extract_addresses(message: dict[str, Any]) -> set[str]:
    """Return normalized address candidates from a DSN without rendering it."""
    sources = [
        message.get("text_body") or "",
        message.get("headers_json") or "",
        message.get("message_json") or "",
    ]
    return {address.lower() for address in EMAIL_RE.findall("\n".join(sources))}


def bounce_messages(client: httpx.Client, base_url: str, limit: int, token: str) -> Iterable[dict[str, Any]]:
    headers = {"Authorization": f"Bearer {token}"}
    try:
        listing = client.get(f"{base_url.rstrip('/')}/api/emails", params={"limit": limit}, headers=headers)
        listing.raise_for_status()
        emails = listing.json().get("emails", [])
        if not isinstance(emails, list):
            raise ValueError("monitor returned an invalid email listing")
        for summary in emails:
            if not isinstance(summary, dict) or summary.get("subject") != BOUNCE_SUBJECT:
                continue
            email_id = summary.get("email_id")
            if not isinstance(email_id, str) or not email_id:
                continue
            response = client.get(f"{base_url.rstrip('/')}/api/emails/{email_id}", headers=headers)
            response.raise_for_status()
            message = response.json()
            if isinstance(message, dict):
                yield message
    except (httpx.HTTPError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"could not read Resend inbound monitor: {exc}") from exc


@dataclass
class SuppressionMatch:
    source: str
    marker: str
    reason: str
    occurred_at: datetime
    recipients: list[Recipient]


def _short_reason(value: str, fallback: str) -> str:
    condensed = " ".join(value.split())
    return condensed[:240] if condensed else fallback


def bounce_reason(message: dict[str, Any]) -> str:
    """Extract the most useful DSN diagnostic without displaying mail content."""
    body = str(message.get("text_body") or "")
    diagnostic = re.search(r"^Diagnostic-Code:\s*(.+)$", body, re.IGNORECASE | re.MULTILINE)
    if diagnostic:
        return _short_reason(f"Resend bounce: {diagnostic.group(1)}", "Resend bounce")
    status = re.search(r"^Status:\s*([245]\.\d\.\d)\s*$", body, re.IGNORECASE | re.MULTILINE)
    if status:
        return f"Resend bounce: DSN {status.group(1)}"
    return "Resend bounce: Undelivered Mail Returned to Sender"


def smtp_failure_reason(attempt: DeliveryAttempt) -> str:
    prefix = f"SMTP {attempt.smtp_code}" if attempt.smtp_code else "SMTP delivery failure"
    return _short_reason(f"{prefix}: {attempt.detail or ''}", prefix)


def _known_marker(db: Session, marker: str) -> BounceEvent | None:
    # Older imports stored the marker in diagnostic; retain their idempotency.
    return db.scalar(
        select(BounceEvent).where(or_(BounceEvent.source_marker == marker, BounceEvent.diagnostic == marker))
    )


def _bounce_received_at(message: dict[str, Any]) -> datetime:
    value = message.get("received_at")
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, timezone.utc)
    return datetime.now(timezone.utc)


def find_new_bounces(db: Session, messages: Iterable[dict[str, Any]]) -> list[SuppressionMatch]:
    """Find unsuppressed Mailmerge recipients referenced by new DSN messages."""
    matches: list[SuppressionMatch] = []
    for message in messages:
        source_id = message.get("email_id")
        if not isinstance(source_id, str) or not source_id:
            continue
        candidates = extract_addresses(message)
        recipients = db.scalars(
            select(Recipient)
            .join(Campaign, Recipient.campaign_id == Campaign.id)
            .where(Recipient.normalized_email.in_(candidates), Campaign.state != CampaignState.sending)
        ).all() if candidates else []
        marker = f"resend-inbound:{source_id}"
        known_bounce = _known_marker(db, marker)
        recipients = [recipient for recipient in recipients if not recipient.suppressed]
        if known_bounce or not recipients:
            continue
        matches.append(SuppressionMatch(
            source="Resend bounce", marker=marker, reason=bounce_reason(message),
            occurred_at=_bounce_received_at(message), recipients=recipients
        ))
    return matches


def find_new_resend_bounces(db: Session, monitor_url: str, token: str, limit: int = 200) -> list[SuppressionMatch]:
    """Fetch and inspect stored Resend DSNs for an interactive reviewer."""
    with httpx.Client(timeout=15.0) as client:
        return find_new_bounces(db, bounce_messages(client, monitor_url, limit, token))


def find_new_smtp_failures(db: Session) -> list[SuppressionMatch]:
    """Find unsuppressed addresses whose most recent delivery attempt failed."""
    matches: list[SuppressionMatch] = []
    seen_addresses: set[str] = set()
    attempts = db.execute(
        select(DeliveryAttempt, Recipient)
        .join(Recipient, DeliveryAttempt.recipient_id == Recipient.id)
        .where(Recipient.status == "failed", DeliveryAttempt.outcome != "sent")
        .order_by(DeliveryAttempt.id.desc())
    ).all()
    for attempt, failed_recipient in attempts:
        address = failed_recipient.normalized_email
        if address in seen_addresses:
            continue
        seen_addresses.add(address)
        marker = f"smtp-failure:{attempt.id}"
        if _known_marker(db, marker):
            continue
        recipients = db.scalars(
            select(Recipient)
            .join(Campaign, Recipient.campaign_id == Campaign.id)
            .where(
                Recipient.normalized_email == address,
                ~Recipient.suppressed,
                Campaign.state != CampaignState.sending,
            )
        ).all()
        if recipients:
            matches.append(SuppressionMatch(
                source="SMTP failure", marker=marker, reason=smtp_failure_reason(attempt),
                occurred_at=attempt.attempted_at, recipients=recipients
            ))
    return matches


def apply_suppressions(db: Session, matches: Iterable[SuppressionMatch]) -> int:
    """Apply an operator-confirmed set of bounce and SMTP-failure suppressions."""
    matched_records = 0
    for match in matches:
        for recipient in match.recipients:
            if recipient.suppressed:
                continue
            recipient.suppressed = True
            matched_records += 1
            db.add(BounceEvent(
                recipient_id=recipient.id,
                kind=match.source,
                source_marker=match.marker,
                diagnostic=match.reason,
                received_at=match.occurred_at,
                recognized=True,
            ))
            db.add(AuditLog(
                campaign_id=recipient.campaign_id,
                action="bounce-suppressed",
                detail={"email": recipient.normalized_email, "source": match.marker, "reason": match.reason},
            ))
    return matched_records


def main() -> None:
    parser = argparse.ArgumentParser(description="Suppress Mailmerge recipients found in Resend DSNs or final SMTP failures.")
    parser.add_argument("--monitor-url", default=os.getenv("MAILMERGE_RESEND_MONITOR_URL", "http://127.0.0.1:8089"))
    parser.add_argument("--limit", type=int, default=200, help="Number of newest monitor messages to inspect (maximum 200).")
    parser.add_argument("--dry-run", action="store_true", help="List new bounce recipients without asking for confirmation.")
    args = parser.parse_args()
    if not os.getenv("MAILMERGE_RESEND_MONITOR_API_TOKEN"):
        parser.error("set MAILMERGE_RESEND_MONITOR_API_TOKEN; do not pass the token on the command line")
    if not 1 <= args.limit <= 200:
        parser.error("--limit must be between 1 and 200")

    init_db()
    with httpx.Client(timeout=15.0) as client:
        messages = list(bounce_messages(client, args.monitor_url, args.limit, os.environ["MAILMERGE_RESEND_MONITOR_API_TOKEN"]))
    with SessionLocal() as db:
        bounce_matches = find_new_bounces(db, messages)
        smtp_matches = find_new_smtp_failures(db)
        matches = [*bounce_matches, *smtp_matches]
        bounce_addresses = sorted({recipient.normalized_email for match in bounce_matches for recipient in match.recipients})
        smtp_addresses = sorted({recipient.normalized_email for match in smtp_matches for recipient in match.recipients})
        addresses = sorted(set(bounce_addresses) | set(smtp_addresses))
        if not addresses:
            print("No new bounced or SMTP-failed recipients need suppression.")
            return
        if bounce_addresses:
            print(f"New Resend bounce recipients ({len(bounce_addresses)}):")
            for address in bounce_addresses:
                print(address)
        if smtp_addresses:
            print(f"New SMTP-failed recipients ({len(smtp_addresses)}):")
            for address in smtp_addresses:
                print(address)
        print(f"Total new suppression candidates: {len(addresses)}")
        for address in sorted(set(bounce_addresses) & set(smtp_addresses)):
            print(f"{address} (appears in both sources)")
        if args.dry_run:
            return
        if not sys.stdin.isatty():
            print("No changes made: confirmation requires an interactive terminal.")
            return
        answer = input(f"Add these {len(addresses)} address(es) to the suppression list? [y/N] ").strip().lower()
        if answer not in {"y", "yes"}:
            print("No changes made.")
            return
        updated = apply_suppressions(db, matches)
        db.commit()
    print(f"Suppressed {updated} recipient record(s) from {len(matches)} new source event(s).")


if __name__ == "__main__":
    main()
