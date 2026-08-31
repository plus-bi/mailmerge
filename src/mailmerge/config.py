from __future__ import annotations

import os
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
        return self

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.data_dir / 'mailmerge.sqlite3'}"

    @property
    def profile_config_path(self) -> Path:
        """Configured profile file, or the UI-managed file in the application data directory."""
        return self.profile_config or self.data_dir / "profiles.toml"


settings = Settings().prepare()
