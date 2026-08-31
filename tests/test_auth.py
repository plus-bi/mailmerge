from unittest.mock import MagicMock, patch
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mailmerge.auth import get_jwks_url, verify_clerk_token
from mailmerge.main import LocalSecurityMiddleware


def test_get_jwks_url_from_publishable_key(monkeypatch):
    monkeypatch.setenv("CLERK_PUBLISHABLE_KEY", "pk_test_dGVzdC5jbGVyay5hY2NvdW50cy5kZXYk")
    import mailmerge.auth as auth_mod

    auth_mod._jwks_url = None
    url = get_jwks_url()
    assert url == "https://test.clerk.accounts.dev/.well-known/jwks.json"


def test_verify_clerk_token_mocked():
    with patch("mailmerge.auth.get_jwks_url", return_value="https://example.clerk.accounts.dev/.well-known/jwks.json"):
        with patch("mailmerge.auth.jwt.PyJWKClient") as mock_client_cls:
            mock_client = MagicMock()
            mock_key = MagicMock()
            mock_key.key = "public_key"
            mock_client.get_signing_key_from_jwt.return_value = mock_key
            mock_client_cls.return_value = mock_client

            with patch("mailmerge.auth.jwt.decode", return_value={"sub": "user_123"}):
                assert verify_clerk_token("valid.jwt.token") is True

            with patch("mailmerge.auth.jwt.decode", side_effect=Exception("Expired")):
                assert verify_clerk_token("expired.jwt.token") is False


def test_local_security_middleware_with_clerk(monkeypatch):
    app = FastAPI()
    app.add_middleware(LocalSecurityMiddleware)

    @app.get("/api/test")
    def api_test():
        return {"ok": True}

    with TestClient(app) as client:
        # 1. Without a Clerk session -> 401
        res = client.get("/api/test")
        assert res.status_code == 401

        # 2. With a valid Clerk bearer token
        with patch("mailmerge.main.verify_clerk_token", return_value=True):
            res = client.get("/api/test", headers={"Authorization": "Bearer clerk_token_123"})
            assert res.status_code == 200
            assert res.json() == {"ok": True}

        # 3. Clerk's same-origin session cookie authenticates browser EventSource requests.
        with patch("mailmerge.main.verify_clerk_token", return_value=True):
            res = client.get("/api/test", cookies={"__session": "clerk_token_123"})
            assert res.status_code == 200
            assert res.json() == {"ok": True}

        # 4. Legacy URL/session tokens are not accepted.
        with patch("mailmerge.main.verify_clerk_token", return_value=True) as verify:
            res = client.get("/api/test?token=legacy-token")
            assert res.status_code == 401
            verify.assert_not_called()
