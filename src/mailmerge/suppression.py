from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .models import Campaign, Recipient, SyncCursor


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

    # Try HTTP sync first if configured
    if url and secret:
        try:
            with httpx.Client(timeout=10.0) as client:
                res = client.get(url, params={"cursor": last_cursor}, headers={"Authorization": f"Bearer {secret}"})
                if res.status_code == 200:
                    data = res.json()
                    synced_events = data.get("events", [])
                    new_cursor = int(data.get("cursor", last_cursor))
        except Exception:
            pass

    # Fallback to local SQLite database if reachable
    if not synced_events and unsub_db and Path(unsub_db).exists():
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
        except Exception:
            pass

    count = 0
    for event in synced_events:
        count += 1
        recipient_id = event.get("recipient_id")
        recipient = db.get(Recipient, recipient_id) if recipient_id else None
        if recipient:
            recipient.suppressed = True
            norm_email = recipient.normalized_email
            if norm_email:
                db.query(Recipient).filter(Recipient.normalized_email == norm_email).update({"suppressed": True})

    cursor_record.cursor = str(new_cursor)
    cursor_record.updated_at = datetime.now(timezone.utc)

    # Mark campaigns as suppression_synced
    db.query(Campaign).update({"suppression_synced": True})
    db.commit()
    return count
