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


def render_page(
    title: str,
    message: str,
    *,
    show_confirm: bool = False,
    show_home_cta: bool = False,
) -> str:
    action = """
        <form method="post">
          <button type="submit">Confirm unsubscribe</button>
        </form>
    """ if show_confirm else ""
    home_cta = """
        <a class="home-cta" href="https://plus.bi/" aria-label="Visit the Plus BI homepage">
          <img src="https://plus.bi/spd_logo.png" alt="Plus BI">
          <span>Visit plus.bi</span>
        </a>
    """ if show_home_cta else ""
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta name="robots" content="noindex, nofollow">
    <title>{title} · Plus BI</title>
    <style>
      :root {{ color-scheme: dark; --bg: #111522; --card: #181e2c; --border: #2b3346; --text: #f1f5f9; --muted: #8f9aad; --primary: #00d6ad; }}
      * {{ box-sizing: border-box; }}
      body {{ margin: 0; min-height: 100vh; display: grid; place-items: center; padding: 24px; background: radial-gradient(ellipse at 50% 0%, rgba(0,214,173,.14), transparent 55%), var(--bg); color: var(--text); font-family: Inter, system-ui, sans-serif; }}
      main {{ width: min(100%, 520px); padding: 40px; border: 1px solid var(--border); border-radius: 16px; background: linear-gradient(145deg, rgba(29,36,52,.96), rgba(20,25,38,.96)); box-shadow: 0 24px 70px rgba(0,0,0,.3); text-align: center; }}
      .home-cta {{ display: inline-flex; flex-direction: column; align-items: center; gap: 12px; margin: 0 0 28px; color: var(--primary); font-weight: 700; text-decoration: none; }}
      .home-cta img {{ display: block; width: min(220px, 70vw); max-height: 88px; object-fit: contain; }}
      .home-cta:hover span {{ text-decoration: underline; }}
      h1 {{ margin: 0 0 12px; font-size: clamp(1.75rem, 5vw, 2.35rem); line-height: 1.15; }}
      p {{ margin: 0 auto 28px; max-width: 42ch; color: var(--muted); line-height: 1.65; }}
      button {{ border: 0; border-radius: 10px; padding: 12px 20px; background: var(--primary); color: #08130f; font: inherit; font-weight: 700; cursor: pointer; }}
      button:hover {{ filter: brightness(1.08); }}
      a {{ color: var(--primary); }}
    </style>
  </head>
  <body>
    <main>
      {home_cta}
      <h1>{title}</h1>
      <p>{message}</p>
      {action}
    </main>
  </body>
</html>"""


@app.get("/health")
def health():
    return {"ok": bool(SIGNING_SECRET and SYNC_SECRET)}


@app.get("/u/{token}", response_class=HTMLResponse)
def confirmation(token: str):
    try:
        verify_token(token)
    except HTTPException:
        return HTMLResponse(
            render_page("Link unavailable", "This unsubscribe link is invalid or incomplete."),
            status_code=404,
        )
    return render_page(
        "Unsubscribe from emails",
        "Confirm that you no longer want to receive emails from this sender.",
        show_confirm=True,
        show_home_cta=True,
    )


@app.post("/u/{token}", response_class=HTMLResponse)
async def unsubscribe(token: str, request: Request):
    try:
        data = verify_token(token)
    except HTTPException:
        return HTMLResponse(
            render_page("Link unavailable", "This unsubscribe link is invalid or incomplete."),
            status_code=404,
        )
    content_type = request.headers.get("content-type", "")
    if "application/x-www-form-urlencoded" in content_type:
        body = (await request.body()).decode(errors="replace")
        if body and body != "List-Unsubscribe=One-Click":
            raise HTTPException(400, "invalid one-click request")
    with connect() as db:
        db.execute("INSERT OR IGNORE INTO events(campaign_id, recipient_id, created_at) VALUES (?, ?, ?)",
                   (data["c"], data["r"], int(time.time())))
    return render_page(
        "You’re unsubscribed",
        "Your preference has been saved. You will no longer receive emails from this campaign.",
        show_home_cta=True,
    )


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
