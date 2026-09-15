from __future__ import annotations

from sqlalchemy import create_engine, event, inspect, text
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
            ("campaigns", "working_hours_start_minute", "INTEGER DEFAULT 0"),
            ("campaigns", "working_hours_end_minute", "INTEGER DEFAULT 0"),
            ("campaigns", "follow_up_source_id", "VARCHAR"),
            ("campaigns", "is_follow_up", "BOOLEAN DEFAULT 0"),
            ("recipients", "reply_to_message_id", "VARCHAR(255)"),
            ("recipients", "source_recipient_id", "VARCHAR"),
            ("recipients", "thread_references", "JSON"),
            ("recipients", "exclusion_reason", "TEXT"),
            ("unsubscribe_events", "campaign_id", "VARCHAR"),
            ("unsubscribe_events", "reason", "VARCHAR(100) DEFAULT 'Unsubscribed'"),
            ("bounce_events", "source_marker", "VARCHAR(255)"),
        ]:
            try:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}"))
                conn.commit()
            except Exception:
                pass

        # Personalized messages are derived from the campaign template and the
        # recipient values at send/preview time. Older releases retained two
        # duplicate rendered-text columns per recipient; remove both the data
        # and columns during startup migration.
        if engine.dialect.name == "sqlite":
            recipient_columns = {column["name"] for column in inspect(conn).get_columns("recipients")}
            for column in ("rendered_subject", "rendered_markdown"):
                if column in recipient_columns:
                    conn.execute(text(f"ALTER TABLE recipients DROP COLUMN {column}"))
            conn.commit()

        conn.execute(
            text(
                "UPDATE campaigns SET unsubscribe_base_url = :new_url "
                "WHERE unsubscribe_base_url IS NULL OR unsubscribe_base_url = :old_url"
            ),
            {"new_url": "https://unsub.plus.bi", "old_url": "https://mailmerge.plus.bi"},
        )
        # Follow-up threading was introduced after recipients already existed.
        # SQLite adds the JSON column as NULL for those rows, while the API
        # contract correctly exposes a list. Normalize them during startup so
        # loading an older campaign cannot fail response validation.
        conn.execute(text("UPDATE recipients SET thread_references = '[]' WHERE thread_references IS NULL"))
        conn.execute(text("UPDATE unsubscribe_events SET reason = 'Unsubscribed' WHERE reason IS NULL OR reason = ''"))
        conn.execute(text("UPDATE campaigns SET working_hours_start_minute = 0 WHERE working_hours_start_minute IS NULL"))
        conn.execute(text("UPDATE campaigns SET working_hours_end_minute = 0 WHERE working_hours_end_minute IS NULL"))
        conn.commit()
