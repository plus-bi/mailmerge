from __future__ import annotations

import keyring

SERVICE = "local-mail-merge"


def set_secret(profile_id: str, kind: str, value: str) -> None:
    keyring.set_password(SERVICE, f"{profile_id}:{kind}", value)


def get_secret(profile_id: str, kind: str) -> str | None:
    return keyring.get_password(SERVICE, f"{profile_id}:{kind}")


def delete_secret(profile_id: str, kind: str) -> None:
    try:
        keyring.delete_password(SERVICE, f"{profile_id}:{kind}")
    except keyring.errors.PasswordDeleteError:
        pass
