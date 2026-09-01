from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def now() -> datetime:
    return datetime.now(timezone.utc)


class CampaignState(str, enum.Enum):
    draft = "draft"
    scheduled = "scheduled"
    awaiting_confirmation = "awaiting_confirmation"
    sending = "sending"
    paused = "paused"
    completed = "completed"
    cancelled = "cancelled"
    failed = "failed"


class Profile(Base):
    __tablename__ = "profiles"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(200))
    from_name: Mapped[str | None] = mapped_column(String(200), default="")
    from_address: Mapped[str | None] = mapped_column(String(320), default="")
    smtp_host: Mapped[str] = mapped_column(String(255))
    smtp_port: Mapped[int] = mapped_column(Integer, default=587)
    security: Mapped[str] = mapped_column(String(20), default="starttls")
    verify_tls: Mapped[bool] = mapped_column(Boolean, default=True)
    username: Mapped[str | None] = mapped_column(String(320))
    auth_type: Mapped[str] = mapped_column(String(20), default="password")
    daily_cap: Mapped[int] = mapped_column(Integer, default=250)
    delay_seconds: Mapped[int] = mapped_column(Integer, default=2)
    max_message_bytes: Mapped[int] = mapped_column(Integer, default=20_000_000)
    reply_to: Mapped[str | None] = mapped_column(String(320))
    list_unsubscribe: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    list_unsubscribe_one_click: Mapped[bool] = mapped_column(Boolean, default=False)
    working_hours_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    working_hours_start: Mapped[int] = mapped_column(Integer, default=9)
    working_hours_end: Mapped[int] = mapped_column(Integer, default=17)
    working_hours_timezone: Mapped[str] = mapped_column(String(50), default="UTC")
    imap_host: Mapped[str | None] = mapped_column(String(255))
    imap_port: Mapped[int | None] = mapped_column(Integer)
    imap_security: Mapped[str | None] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Campaign(Base):
    __tablename__ = "campaigns"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(200))
    purpose: Mapped[str] = mapped_column(String(20), default="operational")
    profile_id: Mapped[str | None] = mapped_column(ForeignKey("profiles.id"))
    from_name: Mapped[str] = mapped_column(String(200), default="")
    from_address: Mapped[str] = mapped_column(String(320), default="")
    reply_to: Mapped[str | None] = mapped_column(String(320))
    subject_template: Mapped[str] = mapped_column(Text, default="")
    body_mode: Mapped[str] = mapped_column(String(20), default="markdown")
    body_template: Mapped[str] = mapped_column(Text, default="")
    state: Mapped[CampaignState] = mapped_column(Enum(CampaignState), default=CampaignState.draft)
    delay_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    working_hours_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    working_hours_start: Mapped[int] = mapped_column(Integer, default=9)
    working_hours_end: Mapped[int] = mapped_column(Integer, default=17)
    working_hours_timezone: Mapped[str] = mapped_column(String(50), default="UTC")
    consent_acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)
    suppression_synced: Mapped[bool] = mapped_column(Boolean, default=False)
    list_unsubscribe_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    unsubscribe_base_url: Mapped[str | None] = mapped_column(String(500), default="https://unsub.plus.bi")
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    guardrail_override: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    recipients: Mapped[list["Recipient"]] = relationship(cascade="all, delete-orphan")
    attachments: Mapped[list["Attachment"]] = relationship(cascade="all, delete-orphan")


class Recipient(Base):
    __tablename__ = "recipients"
    __table_args__ = (UniqueConstraint("campaign_id", "normalized_email"),)
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"), index=True)
    email: Mapped[str] = mapped_column(String(320))
    normalized_email: Mapped[str] = mapped_column(String(320))
    values: Mapped[dict] = mapped_column(JSON, default=dict)
    included: Mapped[bool] = mapped_column(Boolean, default=True)
    valid: Mapped[bool] = mapped_column(Boolean, default=True)
    validation_error: Mapped[str | None] = mapped_column(Text)
    suppressed: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    message_id: Mapped[str | None] = mapped_column(String(255))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Attachment(Base):
    __tablename__ = "attachments"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"))
    filename: Mapped[str] = mapped_column(String(255))
    path: Mapped[str] = mapped_column(Text)
    content_type: Mapped[str] = mapped_column(String(200))
    size: Mapped[int] = mapped_column(Integer)


class DeliveryAttempt(Base):
    __tablename__ = "delivery_attempts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    recipient_id: Mapped[str] = mapped_column(ForeignKey("recipients.id", ondelete="CASCADE"), index=True)
    attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    outcome: Mapped[str] = mapped_column(String(30))
    smtp_code: Mapped[int | None] = mapped_column(Integer)
    detail: Mapped[str | None] = mapped_column(Text)
    retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    campaign_id: Mapped[str | None] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    action: Mapped[str] = mapped_column(String(100))
    detail: Mapped[dict] = mapped_column(JSON, default=dict)


class BounceEvent(Base):
    __tablename__ = "bounce_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    recipient_id: Mapped[str | None] = mapped_column(ForeignKey("recipients.id", ondelete="SET NULL"))
    kind: Mapped[str] = mapped_column(String(20))
    diagnostic: Mapped[str | None] = mapped_column(Text)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    recognized: Mapped[bool] = mapped_column(Boolean, default=False)


class SyncCursor(Base):
    __tablename__ = "sync_cursors"
    name: Mapped[str] = mapped_column(String, primary_key=True)
    cursor: Mapped[str] = mapped_column(String, default="0")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
