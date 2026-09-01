import asyncio
import importlib

from starlette.requests import Request


def load_service(tmp_path, monkeypatch):
    monkeypatch.setenv("UNSUBSCRIBE_DB", str(tmp_path / "events.db"))
    monkeypatch.setenv("UNSUBSCRIBE_SIGNING_SECRET", "signing-test-secret")
    monkeypatch.setenv("UNSUBSCRIBE_SYNC_SECRET", "sync-test-secret")
    import unsubscribe_service.main as service
    return importlib.reload(service)


def request() -> Request:
    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    return Request({"type": "http", "method": "POST", "path": "/", "headers": []}, receive)


def test_token_tampering_is_rejected(tmp_path, monkeypatch):
    service = load_service(tmp_path, monkeypatch)
    token = service.sign_token("campaign-id", "recipient-id")
    assert service.verify_token(token)["r"] == "recipient-id"
    response = service.confirmation(token + "x")
    assert response.status_code == 404
    body = response.body.decode()
    assert "Link unavailable" in body
    assert "PLUS BI" in body


def test_unsubscribe_is_idempotent_and_cursor_based(tmp_path, monkeypatch):
    service = load_service(tmp_path, monkeypatch)
    token = service.sign_token("campaign-id", "recipient-id")
    confirmation = service.confirmation(token)
    assert "Confirm unsubscribe" in confirmation
    assert "PLUS BI" in confirmation
    for _ in range(2):
        unsubscribed = asyncio.run(service.unsubscribe(token, request()))
        assert "You’re unsubscribed" in unsubscribed
    response = service.events(cursor=0, authorization="Bearer sync-test-secret")
    assert len(response["events"]) == 1
    cursor = response["cursor"]
    assert service.events(cursor=cursor, authorization="Bearer sync-test-secret")["events"] == []
