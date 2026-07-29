import importlib

from fastapi.testclient import TestClient


def load_service(tmp_path, monkeypatch):
    monkeypatch.setenv("UNSUBSCRIBE_DB", str(tmp_path / "events.db"))
    monkeypatch.setenv("UNSUBSCRIBE_SIGNING_SECRET", "signing-test-secret")
    monkeypatch.setenv("UNSUBSCRIBE_SYNC_SECRET", "sync-test-secret")
    import unsubscribe_service.main as service
    return importlib.reload(service)


def test_token_tampering_is_rejected(tmp_path, monkeypatch):
    service = load_service(tmp_path, monkeypatch)
    token = service.sign_token("campaign-id", "recipient-id")
    assert service.verify_token(token)["r"] == "recipient-id"
    client = TestClient(service.app)
    assert client.get("/u/" + token + "x").status_code == 404


def test_unsubscribe_is_idempotent_and_cursor_based(tmp_path, monkeypatch):
    service = load_service(tmp_path, monkeypatch)
    token = service.sign_token("campaign-id", "recipient-id")
    client = TestClient(service.app)
    for _ in range(2):
        assert client.post("/u/" + token).status_code == 200
    response = client.get("/api/v1/events?cursor=0", headers={"Authorization": "Bearer sync-test-secret"})
    assert response.status_code == 200
    assert len(response.json()["events"]) == 1
    cursor = response.json()["cursor"]
    assert client.get(f"/api/v1/events?cursor={cursor}", headers={"Authorization": "Bearer sync-test-secret"}).json()["events"] == []

