from __future__ import annotations

import asyncio
import json
import shutil
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from jinja2 import TemplateError
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import settings
from .csv_import import parse_csv
from .db import SessionLocal, get_db
from .messages import build_message
from .models import Attachment, AuditLog, Campaign, CampaignState, DeliveryAttempt, Profile, Recipient
from .profile_config import load_profiles
from .rendering import render_message
from .secrets import set_secret

router = APIRouter(prefix="/api/v1")


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ProfileIn(BaseModel):
    name: str
    smtp_host: str
    smtp_port: int = 587
    security: str = "starttls"
    verify_tls: bool = True
    username: str | None = None
    auth_type: str = "password"
    password: str | None = None
    daily_cap: int = Field(default=250, ge=1)
    delay_seconds: int = Field(default=2, ge=0)
    max_message_bytes: int = Field(default=20_000_000, ge=1024)
    imap_host: str | None = None
    imap_port: int | None = None
    imap_security: str | None = None


class ProfileOut(ORMModel):
    id: str
    name: str
    smtp_host: str
    smtp_port: int
    security: str
    verify_tls: bool
    username: str | None
    auth_type: str
    daily_cap: int
    delay_seconds: int
    max_message_bytes: int
    imap_host: str | None
    imap_port: int | None
    imap_security: str | None


class CampaignIn(BaseModel):
    name: str
    purpose: str = "operational"
    profile_id: str | None = None
    from_name: str = ""
    from_address: str = ""
    reply_to: str | None = None
    subject_template: str = ""
    body_mode: str = "markdown"
    body_template: str = ""
    consent_acknowledged: bool = False
    unsubscribe_base_url: str | None = None


class CampaignOut(ORMModel):
    id: str
    name: str
    purpose: str
    profile_id: str | None
    state: CampaignState
    subject_template: str
    body_mode: str
    body_template: str
    scheduled_at: datetime | None


@router.get("/profiles", response_model=list[ProfileOut])
def profiles(db: Session = Depends(get_db)):
    return db.scalars(select(Profile).order_by(Profile.name)).all()


@router.post("/profiles", response_model=ProfileOut)
def create_profile(data: ProfileIn, db: Session = Depends(get_db)):
    values = data.model_dump(exclude={"password"})
    profile = Profile(**values)
    db.add(profile)
    db.commit()
    if data.password:
        set_secret(profile.id, "password", data.password)
    return profile


@router.post("/profiles/lrz", response_model=ProfileOut)
def create_lrz_profile(db: Session = Depends(get_db)):
    profile = Profile(name="LRZ", smtp_host="postout.lrz.de", smtp_port=587, security="starttls", daily_cap=250)
    db.add(profile)
    db.commit()
    return profile


@router.post("/profiles/reload", response_model=list[ProfileOut])
def reload_profile_config(db: Session = Depends(get_db)):
    if not settings.profile_config:
        raise HTTPException(409, "MAILMERGE_PROFILE_CONFIG is not configured")
    try:
        return load_profiles(settings.profile_config, db)
    except (OSError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/campaigns", response_model=list[CampaignOut])
def campaigns(db: Session = Depends(get_db)):
    return db.scalars(select(Campaign).order_by(Campaign.created_at.desc())).all()


@router.post("/campaigns", response_model=CampaignOut)
def create_campaign(data: CampaignIn, db: Session = Depends(get_db)):
    if data.purpose not in {"operational", "marketing"} or data.body_mode not in {"markdown", "html"}:
        raise HTTPException(422, "invalid purpose or body mode")
    campaign = Campaign(**data.model_dump())
    db.add(campaign)
    db.commit()
    return campaign


@router.get("/campaigns/{campaign_id}", response_model=CampaignOut)
def get_campaign(campaign_id: str, db: Session = Depends(get_db)):
    return _campaign(db, campaign_id)


@router.put("/campaigns/{campaign_id}", response_model=CampaignOut)
def update_campaign(campaign_id: str, data: CampaignIn, db: Session = Depends(get_db)):
    campaign = _campaign(db, campaign_id)
    if campaign.state not in {CampaignState.draft, CampaignState.paused}:
        raise HTTPException(409, "campaign cannot be edited in this state")
    for key, value in data.model_dump().items():
        setattr(campaign, key, value)
    db.commit()
    return campaign


@router.post("/campaigns/{campaign_id}/csv")
async def import_csv(campaign_id: str, email_column: str = Form(...), file: UploadFile = File(...), db: Session = Depends(get_db)):
    campaign = _campaign(db, campaign_id)
    try:
        headers, rows = parse_csv(await file.read(), email_column)
    except (UnicodeDecodeError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc
    db.query(Recipient).filter(Recipient.campaign_id == campaign.id).delete()
    for row in rows:
        db.add(Recipient(campaign_id=campaign.id, email=row.email, normalized_email=row.email.casefold(), values=row.values,
                         included=row.valid, valid=row.valid, validation_error=row.error))
    db.commit()
    return {"headers": headers, "imported": len(rows), "valid": sum(row.valid for row in rows),
            "duplicates": sum(row.duplicate for row in rows)}


@router.post("/campaigns/{campaign_id}/attachments")
async def add_attachment(campaign_id: str, file: UploadFile = File(...), db: Session = Depends(get_db)):
    campaign = _campaign(db, campaign_id)
    attachment_id = str(uuid.uuid4())
    safe_name = Path(file.filename or "attachment").name
    destination = settings.data_dir / "attachments" / f"{attachment_id}-{safe_name}"
    with destination.open("wb") as output:
        shutil.copyfileobj(file.file, output)
    attachment = Attachment(id=attachment_id, campaign_id=campaign.id, filename=safe_name, path=str(destination),
                            content_type=file.content_type or "application/octet-stream", size=destination.stat().st_size)
    db.add(attachment)
    db.commit()
    return {"id": attachment.id, "filename": attachment.filename, "size": attachment.size}


def preflight(campaign: Campaign, db: Session) -> dict:
    errors: list[str] = []
    previews: list[dict] = []
    if not campaign.profile_id:
        errors.append("sender profile is required")
    profile = db.get(Profile, campaign.profile_id) if campaign.profile_id else None
    recipients = db.scalars(select(Recipient).where(Recipient.campaign_id == campaign.id)).all()
    for recipient in recipients:
        if not recipient.included or not recipient.valid or recipient.suppressed:
            continue
        values = dict(recipient.values)
        values.setdefault("email", recipient.email)
        try:
            rendered = render_message(campaign.subject_template, campaign.body_template, campaign.body_mode, values)
            message = build_message(campaign, recipient.email, rendered)
            size = len(message.as_bytes())
            if profile and size > profile.max_message_bytes:
                raise ValueError(f"estimated message size {size} exceeds profile limit")
            if campaign.purpose == "marketing" and "unsubscribe_url" not in values and "unsubscribe_url" not in campaign.body_template:
                raise ValueError("marketing message must visibly include unsubscribe_url")
            previews.append({"recipient_id": recipient.id, "email": recipient.email, "subject": rendered.subject,
                             "html": rendered.html, "text": rendered.text, "size": size})
        except (TemplateError, ValueError) as exc:
            errors.append(f"{recipient.email}: {exc}")
    if not previews:
        errors.append("no sendable recipients")
    if campaign.purpose == "marketing":
        if not campaign.consent_acknowledged:
            errors.append("marketing consent must be acknowledged")
        if not campaign.suppression_synced:
            errors.append("suppression synchronization is required")
        if not campaign.unsubscribe_base_url:
            errors.append("unsubscribe service is required")
    return {"ok": not errors, "errors": errors, "previews": previews,
            "excluded": len(recipients) - len(previews), "attachments": [{"name": a.filename, "size": a.size} for a in campaign.attachments]}


@router.post("/campaigns/{campaign_id}/preflight")
def run_preflight(campaign_id: str, db: Session = Depends(get_db)):
    return preflight(_campaign(db, campaign_id), db)


class ScheduleIn(BaseModel):
    scheduled_at: datetime
    confirm_guardrail_override: bool = False


@router.post("/campaigns/{campaign_id}/schedule")
def schedule(campaign_id: str, data: ScheduleIn, db: Session = Depends(get_db)):
    campaign = _campaign(db, campaign_id)
    result = preflight(campaign, db)
    if not result["ok"]:
        raise HTTPException(409, detail=result["errors"])
    profile = db.get(Profile, campaign.profile_id)
    count = len(result["previews"])
    sent_24h = db.scalar(select(func.count()).select_from(DeliveryAttempt).where(
        DeliveryAttempt.outcome == "sent", DeliveryAttempt.attempted_at >= datetime.now(timezone.utc) - timedelta(hours=24))) or 0
    if sent_24h + count > profile.daily_cap and not data.confirm_guardrail_override:
        raise HTTPException(409, "rolling daily cap exceeded; explicit override confirmation required")
    campaign.guardrail_override = sent_24h + count > profile.daily_cap
    campaign.scheduled_at = data.scheduled_at.astimezone(timezone.utc)
    campaign.state = CampaignState.scheduled
    db.add(AuditLog(campaign_id=campaign.id, action="scheduled", detail={"at": campaign.scheduled_at.isoformat(), "guardrail_override": campaign.guardrail_override}))
    db.commit()
    return {"state": campaign.state, "scheduled_at": campaign.scheduled_at}


@router.post("/campaigns/{campaign_id}/{action}")
def control(campaign_id: str, action: str, db: Session = Depends(get_db)):
    campaign = _campaign(db, campaign_id)
    transitions = {"pause": CampaignState.paused, "resume": CampaignState.scheduled,
                   "cancel": CampaignState.cancelled, "confirm-overdue": CampaignState.scheduled}
    if action not in transitions:
        raise HTTPException(404)
    campaign.state = transitions[action]
    if action in {"resume", "confirm-overdue"}:
        campaign.scheduled_at = datetime.now(timezone.utc)
    db.add(AuditLog(campaign_id=campaign.id, action=action))
    db.commit()
    return {"state": campaign.state}


@router.get("/campaigns/{campaign_id}/events")
async def events(campaign_id: str):
    async def stream():
        while True:
            with SessionLocal() as db:
                campaign = db.get(Campaign, campaign_id)
                if not campaign:
                    yield "event: error\ndata: not found\n\n"
                    return
                counts = dict(db.execute(select(Recipient.status, func.count()).where(Recipient.campaign_id == campaign_id).group_by(Recipient.status)).all())
                yield f"data: {json.dumps({'state': campaign.state.value, 'counts': counts})}\n\n"
                if campaign.state in {CampaignState.completed, CampaignState.cancelled, CampaignState.failed}:
                    return
            await asyncio.sleep(2)
    return StreamingResponse(stream(), media_type="text/event-stream")


def _campaign(db: Session, campaign_id: str) -> Campaign:
    campaign = db.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(404, "campaign not found")
    return campaign
