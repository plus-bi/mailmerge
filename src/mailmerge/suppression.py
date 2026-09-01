from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .models import Campaign, Recipient, SyncCursor, UnsubscribeEvent


def sync_suppressions(db: Session, sync_url: str | None = None, sync_secret: str | None = None) -> int:
    url = sync_url or getattr(settings, "unsubscribe_sync_url", None) or os.getenv("UNSUBSCRIBE_SYNC_URL")
    secret = sync_secret or getattr(settings, "unsubscribe_sync_secret", None) or os.getenv("UNSUBSCRIBE_SYNC_SECRET")
    unsub_db = getattr(settings, "unsubscribe_db", None) or os.getenv("UNSUBSCRIBE_DB")

    cursor_record = db.get(SyncCursor, "unsubscribe_service")
    if not cursor_record:
        cursor_record = SyncCursor(name="unsubscribe_service", cursor="0")
        db.add(cursor_record)
        db.commit()
        db.refresh(cursor_record)

    last_cursor = int(cursor_record.cursor or "0")
    synced_events: list[dict] = []
    new_cursor = last_cursor

    # Use the authenticated HTTP feed when configured.
    if url:
        if not secret:
            raise ValueError("unsubscribe sync secret is not configured")
        try:
            with httpx.Client(timeout=10.0) as client:
                res = client.get(url, params={"cursor": last_cursor}, headers={"Authorization": f"Bearer {secret}"})
                res.raise_for_status()
                data = res.json()
                synced_events = data.get("events", [])
                new_cursor = int(data.get("cursor", last_cursor))
        except (httpx.HTTPError, ValueError) as exc:
            raise RuntimeError(f"unsubscribe service sync failed: {exc}") from exc

    # A local SQLite source is useful for single-host development.
    elif unsub_db and Path(unsub_db).exists():
        try:
            with sqlite3.connect(unsub_db) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT id, campaign_id, recipient_id, created_at FROM events WHERE id > ? ORDER BY id LIMIT 1000",
                    (last_cursor,),
                ).fetchall()
                synced_events = [dict(r) for r in rows]
                if synced_events:
                    new_cursor = synced_events[-1]["id"]
        except sqlite3.Error as exc:
            raise RuntimeError(f"unsubscribe database sync failed: {exc}") from exc
    else:
        raise ValueError(
            "unsubscribe sync is not configured; set MAILMERGE_UNSUBSCRIBE_SYNC_URL "
            "and MAILMERGE_UNSUBSCRIBE_SYNC_SECRET"
        )

    count = 0
    for event in synced_events:
        count += 1
        recipient_id = event.get("recipient_id")
        norm_email: str | None = None
        if recipient_id:
            if "@" in recipient_id:
                norm_email = recipient_id.strip().lower()
                db.query(Recipient).filter(Recipient.normalized_email == norm_email).update({"suppressed": True})
            else:
                recipient = db.get(Recipient, recipient_id)
                if recipient:
                    recipient.suppressed = True
                    norm_email = recipient.normalized_email
                    if norm_email:
                        db.query(Recipient).filter(Recipient.normalized_email == norm_email).update({"suppressed": True})
        source_event_id = event.get("id")
        created_at = event.get("created_at")
        if source_event_id is not None and norm_email and created_at is not None:
            source_event_id = int(source_event_id)
            if not db.get(UnsubscribeEvent, source_event_id):
                db.add(
                    UnsubscribeEvent(
                        source_event_id=source_event_id,
                        email=norm_email,
                        campaign=str(event.get("campaign_id") or ""),
                        unsubscribed_at=datetime.fromtimestamp(int(created_at), timezone.utc),
                    )
                )

    cursor_record.cursor = str(new_cursor)
    cursor_record.updated_at = datetime.now(timezone.utc)

    # Mark campaigns as suppression_synced
    db.query(Campaign).update({"suppression_synced": True})
    db.commit()
    return count
