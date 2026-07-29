from __future__ import annotations

import os
import tomllib
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Profile
from .secrets import set_secret

ALLOWED_FIELDS = {
    "smtp_host", "smtp_port", "security", "verify_tls", "username", "auth_type",
    "daily_cap", "delay_seconds", "max_message_bytes", "imap_host", "imap_port", "imap_security",
}


def load_profiles(path: Path, db: Session) -> list[Profile]:
    """Upsert sender profiles from TOML without persisting secrets in SQLite."""
    if not path.is_file():
        raise FileNotFoundError(f"profile configuration not found: {path}")
    with path.open("rb") as handle:
        document = tomllib.load(handle)
    entries = document.get("profiles", [])
    if not isinstance(entries, list):
        raise ValueError("profile configuration must contain [[profiles]] entries")

    loaded: list[Profile] = []
    names: set[str] = set()
    for index, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            raise ValueError(f"profile {index} must be a TOML table")
        name = str(entry.get("name", "")).strip()
        if not name or name in names:
            raise ValueError(f"profile {index} has a missing or duplicate name")
        if "password" in entry or "access_token" in entry or "refresh_token" in entry:
            raise ValueError(f"profile {name!r} contains a secret; use password_env and the OS keychain")
        names.add(name)
        if not entry.get("smtp_host"):
            raise ValueError(f"profile {name!r} requires smtp_host")
        security = entry.get("security", "starttls")
        imap_security = entry.get("imap_security")
        if security not in {"starttls", "tls", "none"}:
            raise ValueError(f"profile {name!r} has invalid SMTP security")
        if imap_security not in {None, "starttls", "tls", "none"}:
            raise ValueError(f"profile {name!r} has invalid IMAP security")

        profile = db.scalar(select(Profile).where(Profile.name == name))
        values = {key: entry[key] for key in ALLOWED_FIELDS if key in entry}
        if profile is None:
            profile = Profile(name=name, **values)
            db.add(profile)
            db.flush()
        else:
            for key, value in values.items():
                setattr(profile, key, value)

        password_env = entry.get("password_env")
        if password_env:
            password = os.getenv(str(password_env))
            if password is None:
                raise ValueError(f"environment variable {password_env!r} required by profile {name!r} is unset")
            set_secret(profile.id, "password", password)
        loaded.append(profile)
    db.commit()
    return loaded
