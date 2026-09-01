from __future__ import annotations

import json
import os
import tempfile
import tomllib
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Profile
from .secrets import set_secret
from .rendering import valid_email

ALLOWED_FIELDS = {
    "from_name", "from_address", "smtp_host", "smtp_port", "security", "verify_tls", "username", "auth_type",
    "daily_cap", "delay_seconds", "max_message_bytes", "reply_to", "list_unsubscribe",
    "list_unsubscribe_one_click", "imap_host", "imap_port", "imap_security",
    "working_hours_enabled", "working_hours_start", "working_hours_end",
    "working_hours_timezone",
}

PROFILE_FIELD_ORDER = (
    "from_name", "from_address", "smtp_host", "smtp_port", "security", "verify_tls", "username", "auth_type",
    "reply_to", "list_unsubscribe", "list_unsubscribe_one_click", "imap_host",
    "imap_port", "imap_security", "daily_cap", "delay_seconds", "max_message_bytes",
    "working_hours_enabled", "working_hours_start", "working_hours_end",
    "working_hours_timezone",
)


def validate_profile_entry(entry: dict, index: int = 1) -> tuple[str, dict]:
    name = str(entry.get("name", "")).strip()
    if not name:
        raise ValueError(f"profile {index} has a missing name")
    if "password" in entry or "access_token" in entry or "refresh_token" in entry:
        raise ValueError(f"profile {name!r} contains a secret; use password_env and the OS keychain")
    if not entry.get("smtp_host"):
        raise ValueError(f"profile {name!r} requires smtp_host")
    from_address = entry.get("from_address")
    if from_address and not valid_email(str(from_address)):
        raise ValueError(f"profile {name!r} has invalid from_address")
    security = entry.get("security", "starttls")
    imap_security = entry.get("imap_security")
    if security not in {"starttls", "tls", "none"}:
        raise ValueError(f"profile {name!r} has invalid SMTP security")
    if imap_security not in {None, "starttls", "tls", "none"}:
        raise ValueError(f"profile {name!r} has invalid IMAP security")
    if entry.get("auth_type", "password") not in {"password", "xoauth2"}:
        raise ValueError(f"profile {name!r} has invalid authentication type")
    for field, minimum, maximum in (
        ("smtp_port", 1, 65535),
        ("imap_port", 1, 65535),
        ("daily_cap", 1, None),
        ("delay_seconds", 0, None),
        ("max_message_bytes", 1024, None),
        ("working_hours_start", 0, 23),
        ("working_hours_end", 0, 23),
    ):
        value = entry.get(field)
        if value is not None and (
            not isinstance(value, int) or isinstance(value, bool) or value < minimum or (maximum is not None and value > maximum)
        ):
            requirement = f"between {minimum} and {maximum}" if maximum is not None else f"at least {minimum}"
            raise ValueError(f"profile {name!r} {field} must be an integer {requirement}")
    reply_to = entry.get("reply_to")
    if reply_to and not valid_email(str(reply_to)):
        raise ValueError(f"profile {name!r} has invalid reply_to")
    unsubscribe = entry.get("list_unsubscribe")
    if unsubscribe:
        parsed = urlparse(str(unsubscribe).strip("<>"))
        if parsed.scheme not in {"https", "mailto"}:
            raise ValueError(f"profile {name!r} list_unsubscribe must use https or mailto")
    if entry.get("list_unsubscribe_one_click") and (
        not unsubscribe or urlparse(str(unsubscribe).strip("<>")).scheme != "https"
    ):
        raise ValueError(f"profile {name!r} one-click unsubscribe requires an https list_unsubscribe URL")
    return name, {key: entry[key] for key in ALLOWED_FIELDS if key in entry}


def parse_profiles(content: str) -> list[dict]:
    try:
        document = tomllib.loads(content)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"invalid TOML: {exc}") from exc
    entries = document.get("profiles", [])
    if not isinstance(entries, list):
        raise ValueError("profile configuration must contain [[profiles]] entries")
    if not entries:
        raise ValueError("profile configuration does not contain any [[profiles]] entries")
    validated: list[dict] = []
    names: set[str] = set()
    for index, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            raise ValueError(f"profile {index} must be a TOML table")
        name, values = validate_profile_entry(entry, index)
        if name in names:
            raise ValueError(f"profile {index} has a duplicate name")
        names.add(name)
        validated.append({"name": name, **values, "password_env": entry.get("password_env")})
    return validated


def load_profiles_text(content: str, db: Session, *, require_password_env: bool = True) -> list[Profile]:
    """Upsert profiles from TOML text and copy available environment secrets to keyring."""
    entries = parse_profiles(content)
    loaded: list[Profile] = []
    for entry in entries:
        name = entry.pop("name")
        password_env = entry.pop("password_env", None)
        profile = db.scalar(select(Profile).where(Profile.name == name))
        if profile is None:
            profile = Profile(name=name, **entry)
            db.add(profile)
            db.flush()
        else:
            for key, value in entry.items():
                setattr(profile, key, value)
        if password_env:
            password = os.getenv(str(password_env))
            if password is None and require_password_env:
                raise ValueError(f"environment variable {password_env!r} required by profile {name!r} is unset")
            if password is not None:
                set_secret(profile.id, "password", password)
        loaded.append(profile)
    db.commit()
    return loaded


def dump_profiles(profiles: list[Profile]) -> str:
    """Serialize profiles without credentials to portable TOML."""
    lines = [
        "# Mail Merge sender profiles",
        "# Passwords and OAuth tokens are intentionally excluded and remain in the OS keychain.",
        "",
    ]
    for profile in profiles:
        lines.extend(("[[profiles]]", f"name = {json.dumps(profile.name)}"))
        for field in PROFILE_FIELD_ORDER:
            value = getattr(profile, field)
            if value is None:
                continue
            if isinstance(value, bool):
                rendered = "true" if value else "false"
            elif isinstance(value, str):
                rendered = json.dumps(value)
            else:
                rendered = str(value)
            lines.append(f"{field} = {rendered}")
        lines.append("")
    return "\n".join(lines)


def save_profile_file(path: Path, content: str) -> None:
    """Atomically save a validated TOML profile file with user-only permissions."""
    parse_profiles(content)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.NamedTemporaryFile("w", dir=path.parent, prefix=f".{path.name}.", delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(content)
    temporary.chmod(0o600)
    temporary.replace(path)


def load_profiles(path: Path, db: Session) -> list[Profile]:
    """Upsert sender profiles from TOML without persisting secrets in SQLite."""
    if not path.is_file():
        raise FileNotFoundError(f"profile configuration not found: {path}")
    return load_profiles_text(path.read_text(), db)
