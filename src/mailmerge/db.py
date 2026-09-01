from __future__ import annotations

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    pass


engine = create_engine(settings.database_url, connect_args={"check_same_thread": False})


@event.listens_for(engine, "connect")
def _sqlite_pragmas(dbapi_connection, _record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


SessionLocal = sessionmaker(engine, expire_on_commit=False, class_=Session)


def get_db():
    with SessionLocal() as db:
        yield db


def init_db() -> None:
    from . import models  # noqa: F401

    Base.metadata.create_all(engine)

    with engine.connect() as conn:
        for table, col, col_type in [
            ("profiles", "from_name", "VARCHAR(200)"),
            ("profiles", "from_address", "VARCHAR(320)"),
            ("profiles", "list_unsubscribe", "VARCHAR(1000)"),
            ("profiles", "list_unsubscribe_one_click", "BOOLEAN DEFAULT 0"),
            ("campaigns", "list_unsubscribe_enabled", "BOOLEAN DEFAULT 0"),
            ("campaigns", "unsubscribe_base_url", "VARCHAR(500)"),
            ("campaigns", "from_name", "VARCHAR(200) DEFAULT ''"),
            ("campaigns", "from_address", "VARCHAR(320) DEFAULT ''"),
        ]:
            try:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}"))
                conn.commit()
            except Exception:
                pass

