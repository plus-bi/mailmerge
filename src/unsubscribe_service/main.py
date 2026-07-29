from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import sqlite3
import time
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse

DB_PATH = Path(os.getenv("UNSUBSCRIBE_DB", "/data/events.sqlite3"))
SIGNING_SECRET = os.getenv("UNSUBSCRIBE_SIGNING_SECRET", "")
SYNC_SECRET = os.getenv("UNSUBSCRIBE_SYNC_SECRET", "")


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def sign_token(campaign_id: str, recipient_id: str, secret: str = SIGNING_SECRET) -> str:
    payload = json.dumps({"c": campaign_id, "r": recipient_id}, separators=(",", ":")).encode()
    signature = hmac.new(secret.encode(), payload, hashlib.sha256).digest()
    return f"{_b64(payload)}.{_b64(signature)}"


def verify_token(token: str, secret: str = SIGNING_SECRET) -> dict[str, str]:
    try:
        encoded, encoded_signature = token.split(".", 1)
        payload = _unb64(encoded)
        signature = _unb64(encoded_signature)
        expected = hmac.new(secret.encode(), payload, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError
        data = json.loads(payload)
        if set(data) != {"c", "r"}:
            raise ValueError
        return data
    except (ValueError, TypeError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(404, "invalid unsubscribe link") from exc


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("""CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        campaign_id TEXT NOT NULL,
        recipient_id TEXT NOT NULL,
        created_at INTEGER NOT NULL,
        UNIQUE(campaign_id, recipient_id)
    )""")
    return db


app = FastAPI(title="Mail Merge Unsubscribe", docs_url=None, redoc_url=None)


@app.get("/health")
def health():
    return {"ok": bool(SIGNING_SECRET and SYNC_SECRET)}


@app.get("/u/{token}", response_class=HTMLResponse)
def confirmation(token: str):
    verify_token(token)
    return """<!doctype html><html><body><h1>Unsubscribe</h1>
    <form method="post"><button type="submit">Confirm unsubscribe</button></form></body></html>"""


@app.post("/u/{token}", response_class=HTMLResponse)
async def unsubscribe(token: str, request: Request):
    data = verify_token(token)
    content_type = request.headers.get("content-type", "")
    if "application/x-www-form-urlencoded" in content_type:
        body = (await request.body()).decode(errors="replace")
        if body and body != "List-Unsubscribe=One-Click":
            raise HTTPException(400, "invalid one-click request")
    with connect() as db:
        db.execute("INSERT OR IGNORE INTO events(campaign_id, recipient_id, created_at) VALUES (?, ?, ?)",
                   (data["c"], data["r"], int(time.time())))
    return "<!doctype html><html><body><h1>You are unsubscribed.</h1></body></html>"


@app.get("/api/v1/events")
def events(cursor: int = 0, authorization: str | None = Header(default=None)):
    if not SYNC_SECRET or not hmac.compare_digest(authorization or "", f"Bearer {SYNC_SECRET}"):
        raise HTTPException(401, "unauthorized")
    with connect() as db:
        rows = db.execute("SELECT id, campaign_id, recipient_id, created_at FROM events WHERE id > ? ORDER BY id LIMIT 1000", (cursor,)).fetchall()
    return {"events": [dict(row) for row in rows], "cursor": rows[-1]["id"] if rows else cursor}


def run() -> None:
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8080)

