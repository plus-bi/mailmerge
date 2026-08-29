from __future__ import annotations

import os
import secrets
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MAILMERGE_", env_file=".env", extra="ignore")

    data_dir: Path = Path.home() / ".local" / "share" / "mailmerge"
    host: str = "127.0.0.1"
    port: int = 8765
    frontend_origin: str = "http://127.0.0.1:8765"
    frontend_dir: Path | None = None
    profile_config: Path | None = None
    session_token: str = ""
    worker_poll_seconds: float = 2.0
    unsubscribe_sync_url: str | None = None
    unsubscribe_sync_secret: str | None = None
    unsubscribe_db: Path | None = None

    def prepare(self) -> "Settings":
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        except OSError:
            self.data_dir = Path("/tmp") / f"mailmerge-{os.getuid()}"
            self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        (self.data_dir / "attachments").mkdir(exist_ok=True, mode=0o700)
        token_file = self.data_dir / "session-token"
        if not self.session_token:
            if token_file.exists():
                self.session_token = token_file.read_text().strip()
            else:
                self.session_token = secrets.token_urlsafe(32)
                token_file.write_text(self.session_token)
                token_file.chmod(0o600)
        return self

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.data_dir / 'mailmerge.sqlite3'}"


settings = Settings().prepare()
