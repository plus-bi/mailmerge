from __future__ import annotations

import base64
import logging
import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
import jwt

logger = logging.getLogger(__name__)

# Load environment files
for candidate in [
    Path("/opt/mailmerge/.env"),
    Path("/opt/mailmerge/frontend/.env.local"),
    Path(__file__).resolve().parents[2] / ".env",
    Path(__file__).resolve().parents[2] / "frontend" / ".env.local",
    Path.cwd() / ".env",
    Path.cwd() / "frontend" / ".env.local",
]:
    if candidate.is_file():
        load_dotenv(candidate)

_jwks_client: Optional[jwt.PyJWKClient] = None
_jwks_url: Optional[str] = None


def get_jwks_url() -> Optional[str]:
    global _jwks_url
    if _jwks_url:
        return _jwks_url

    jwks_env = os.getenv("CLERK_JWKS_URL")
    if jwks_env:
        _jwks_url = jwks_env
        return _jwks_url

    pk = (
        os.getenv("CLERK_PUBLISHABLE_KEY")
        or os.getenv("VITE_CLERK_PUBLISHABLE_KEY")
        or os.getenv("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY")
    )
    if pk and "_" in pk:
        try:
            b64_part = pk.split("_", 2)[2]
            b64_part += "=" * (-len(b64_part) % 4)
            frontend_api = base64.b64decode(b64_part).decode("utf-8").rstrip("$")
            if frontend_api:
                _jwks_url = f"https://{frontend_api}/.well-known/jwks.json"
                logger.info("Configured Clerk JWKS URL: %s", _jwks_url)
                return _jwks_url
        except Exception as exc:
            logger.warning("Failed to derive Clerk JWKS URL from publishable key: %s", exc)

    return None


def verify_clerk_token(token: str) -> bool:
    global _jwks_client
    if not token:
        return False

    jwks_url = get_jwks_url()
    if not jwks_url:
        logger.warning("Clerk token verification failed: JWKS URL not configured")
        return False

    try:
        if not _jwks_client or _jwks_client.uri != jwks_url:
            _jwks_client = jwt.PyJWKClient(jwks_url, cache_keys=True, max_cached_keys=16)

        signing_key = _jwks_client.get_signing_key_from_jwt(token)
        jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256", "EdDSA", "ES256"],
            options={"verify_aud": False, "verify_exp": True},
            leeway=60,
        )
        return True
    except Exception as exc:
        logger.warning("Clerk token verification failed: %s", exc)
        return False
