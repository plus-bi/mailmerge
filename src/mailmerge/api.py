from __future__ import annotations

import asyncio
import json
import shutil
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from jinja2 import TemplateError
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import settings
from .db import SessionLocal, get_db
from .json_import import parse_recipients_json
from .messages import build_message
from .models import Attachment, AuditLog, Campaign, CampaignState, DeliveryAttempt, Profile, Recipient
from .profile_config import dump_profiles, load_profiles, load_profiles_text, save_profile_file, validate_profile_entry
from .rendering import render_message, validate_template_variables
from .secrets import get_secret, set_secret
from .smtp import connect, send
from .suppression import sync_suppressions

router = APIRouter(prefix="/api/v1")


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ProfileIn(BaseModel):
    name: str
    smtp_host: str
    smtp_port: int = Field(default=587, ge=1, le=65535)
    security: Literal["starttls", "tls", "none"] = "starttls"
    verify_tls: bool = True
    username: str | None = None
    auth_type: Literal["password", "xoauth2"] = "password"
    password: str | None = None
    access_token: str | None = None
    daily_cap: int = Field(default=250, ge=1)
    delay_seconds: int = Field(default=2, ge=0)
    max_message_bytes: int = Field(default=20_000_000, ge=1024)
    reply_to: str | None = None
    list_unsubscribe: str | None = None
    list_unsubscribe_one_click: bool = False
    working_hours_enabled: bool = False
    working_hours_start: int = Field(default=9, ge=0, le=23)
    working_hours_end: int = Field(default=17, ge=0, le=23)
    working_hours_timezone: str = "UTC"
    imap_host: str | None = None
    imap_port: int | None = Field(default=None, ge=1, le=65535)
    imap_security: Literal["starttls", "tls", "none"] | None = None


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
    reply_to: str | None
    list_unsubscribe: str | None
    list_unsubscribe_one_click: bool
    working_hours_enabled: bool
    working_hours_start: int
    working_hours_end: int
    working_hours_timezone: str
    imap_host: str | None
    imap_port: int | None
    imap_security: str | None


class ProfileFileIn(BaseModel):
    content: str


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
    delay_seconds: int | None = None
    working_hours_enabled: bool = False
    working_hours_start: int = Field(default=9, ge=0, le=23)
    working_hours_end: int = Field(default=17, ge=0, le=23)
    working_hours_timezone: str = "UTC"
    consent_acknowledged: bool = False
    unsubscribe_base_url: str | None = None


class CampaignOut(ORMModel):
    id: str
    name: str
    purpose: str
    profile_id: str | None
    from_name: str
    from_address: str
    reply_to: str | None
    state: CampaignState
    subject_template: str
    body_mode: str
    body_template: str
    delay_seconds: int | None
    working_hours_enabled: bool
    working_hours_start: int
    working_hours_end: int
    working_hours_timezone: str
    consent_acknowledged: bool
    suppression_synced: bool
    unsubscribe_base_url: str | None
    scheduled_at: datetime | None


class RecipientOut(ORMModel):
    id: str
    campaign_id: str
    email: str
    normalized_email: str
    values: dict[str, Any]
    included: bool
    valid: bool
    validation_error: str | None
    suppressed: bool
    status: str
    message_id: str | None
    sent_at: datetime | None


class TestEmailIn(BaseModel):
    recipient_email: str
    sample_recipient_id: str | None = None


@router.get("/profiles", response_model=list[ProfileOut])
def profiles(db: Session = Depends(get_db)):
    return db.scalars(select(Profile).order_by(Profile.name)).all()


@router.post("/profiles", response_model=ProfileOut)
def create_profile(data: ProfileIn, db: Session = Depends(get_db)):
    values = data.model_dump(exclude={"password", "access_token"})
    try:
        validate_profile_entry({"name": data.name, **values})
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if db.scalar(select(Profile).where(Profile.name == data.name.strip())):
        raise HTTPException(409, "a sender profile with this name already exists")
    values["name"] = data.name.strip()
    profile = Profile(**values)
    db.add(profile)
    db.commit()
    if data.password:
        set_secret(profile.id, "password", data.password)
    if data.access_token:
        set_secret(profile.id, "access_token", data.access_token)
    return profile


@router.put("/profiles/{profile_id}", response_model=ProfileOut)
def update_profile(profile_id: str, data: ProfileIn, db: Session = Depends(get_db)):
    profile = db.get(Profile, profile_id)
    if not profile:
        raise HTTPException(404, "sender profile not found")
    values = data.model_dump(exclude={"password", "access_token"})
    try:
        validate_profile_entry({"name": data.name, **values})
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    duplicate = db.scalar(select(Profile).where(Profile.name == data.name.strip(), Profile.id != profile_id))
    if duplicate:
        raise HTTPException(409, "a sender profile with this name already exists")
    values["name"] = data.name.strip()
    for key, value in values.items():
        setattr(profile, key, value)
    db.commit()
    if data.password:
        set_secret(profile.id, "password", data.password)
    if data.access_token:
        set_secret(profile.id, "access_token", data.access_token)
    return profile


@router.get("/profile-config")
def get_profile_config(db: Session = Depends(get_db)):
    path = settings.profile_config_path
    content = path.read_text() if path.is_file() else dump_profiles(list(db.scalars(select(Profile).order_by(Profile.name)).all()))
    return {"filename": path.name, "content": content, "managed": path.is_file()}


@router.post("/profile-config", response_model=list[ProfileOut])
def import_profile_config(data: ProfileFileIn, db: Session = Depends(get_db)):
    try:
        loaded = load_profiles_text(data.content, db, require_password_env=False)
        save_profile_file(settings.profile_config_path, data.content)
        return loaded
    except (OSError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.put("/profile-config")
def save_profile_config(db: Session = Depends(get_db)):
    profiles = list(db.scalars(select(Profile).order_by(Profile.name)).all())
    if not profiles:
        raise HTTPException(409, "create at least one sender profile before saving TOML")
    content = dump_profiles(profiles)
    try:
        save_profile_file(settings.profile_config_path, content)
    except (OSError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"filename": settings.profile_config_path.name, "content": content}


@router.post("/profiles/lrz", response_model=ProfileOut)
def create_lrz_profile(db: Session = Depends(get_db)):
    profile = Profile(name="LRZ", smtp_host="postout.lrz.de", smtp_port=587, security="starttls", daily_cap=250)
    db.add(profile)
    db.commit()
    return profile


@router.post("/profiles/reload", response_model=list[ProfileOut])
def reload_profile_config(db: Session = Depends(get_db)):
    if not settings.profile_config_path.is_file():
        raise HTTPException(409, "no profile TOML file has been configured or saved")
    try:
        return load_profiles(settings.profile_config_path, db)
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


@router.delete("/campaigns/{campaign_id}")
def delete_campaign(campaign_id: str, db: Session = Depends(get_db)):
    campaign = _campaign(db, campaign_id)
    if campaign.state == CampaignState.sending:
        raise HTTPException(409, "Cannot delete a campaign while it is actively sending. Please pause or cancel it first.")

    # Remove associated attachment files on disk
    for attachment in campaign.attachments:
        try:
            p = Path(attachment.path)
            if p.exists():
                p.unlink()
        except OSError:
            pass

    campaign_name = campaign.name
    db.delete(campaign)
    db.commit()
    return {"ok": True, "id": campaign_id, "message": f"Campaign '{campaign_name}' deleted successfully"}


@router.get("/campaigns/{campaign_id}/recipients", response_model=list[RecipientOut])
def get_recipients(campaign_id: str, db: Session = Depends(get_db)):
    _campaign(db, campaign_id)
    return db.scalars(select(Recipient).where(Recipient.campaign_id == campaign_id).order_by(Recipient.id)).all()


@router.post("/campaigns/{campaign_id}/recipients")
async def import_recipients(campaign_id: str, request: Request, db: Session = Depends(get_db)):
    campaign = _campaign(db, campaign_id)
    content_type = request.headers.get("content-type", "")

    if "multipart/form-data" in content_type:
        form = await request.form()
        uploaded = form.get("file")
        if not uploaded or not hasattr(uploaded, "read"):
            raise HTTPException(422, "file field is required in multipart upload")
        raw_content = await uploaded.read()
    else:
        raw_content = await request.body()

    try:
        keys, rows = parse_recipients_json(
            raw_content,
            subject_template=campaign.subject_template,
            body_template=campaign.body_template,
        )
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(422, f"Failed to parse recipients JSON: {exc}") from exc

    db.query(Recipient).filter(Recipient.campaign_id == campaign.id).delete()
    for row in rows:
        db.add(
            Recipient(
                campaign_id=campaign.id,
                email=row.email,
                normalized_email=row.email.casefold(),
                values=row.values,
                included=row.valid,
                valid=row.valid,
                validation_error=row.error,
            )
        )
    db.commit()
    return {
        "keys": keys,
        "imported": len(rows),
        "valid": sum(1 for r in rows if r.valid),
        "duplicates": sum(1 for r in rows if r.duplicate),
        "errors": [f"{r.email or 'row'}: {r.error}" for r in rows if r.error],
    }


@router.post("/campaigns/{campaign_id}/attachments")
async def add_attachment(campaign_id: str, file: UploadFile = File(...), db: Session = Depends(get_db)):
    campaign = _campaign(db, campaign_id)
    attachment_id = str(uuid.uuid4())
    safe_name = Path(file.filename or "attachment").name
    destination = settings.data_dir / "attachments" / f"{attachment_id}-{safe_name}"
    with destination.open("wb") as output:
        shutil.copyfileobj(file.file, output)
    attachment = Attachment(
        id=attachment_id,
        campaign_id=campaign.id,
        filename=safe_name,
        path=str(destination),
        content_type=file.content_type or "application/octet-stream",
        size=destination.stat().st_size,
    )
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

        # Validate that all required template variables are populated
        missing_vars = validate_template_variables(campaign.subject_template, campaign.body_template, values)
        if missing_vars:
            errors.append(f"{recipient.email}: missing required template variable(s): {', '.join(missing_vars)}")
            continue

        try:
            rendered = render_message(campaign.subject_template, campaign.body_template, campaign.body_mode, values)
            message = build_message(campaign, recipient.email, rendered, profile)
            size = len(message.as_bytes())
            if profile and size > profile.max_message_bytes:
                raise ValueError(f"estimated message size {size} exceeds profile limit")
            if campaign.purpose == "marketing" and "unsubscribe_url" not in values and "unsubscribe_url" not in campaign.body_template:
                raise ValueError("marketing message must visibly include unsubscribe_url")
            previews.append(
                {
                    "recipient_id": recipient.id,
                    "email": recipient.email,
                    "subject": rendered.subject,
                    "html": rendered.html,
                    "text": rendered.text,
                    "size": size,
                    "values": values,
                    "missing_variables": missing_vars,
                }
            )
        except (TemplateError, ValueError) as exc:
            errors.append(f"{recipient.email}: {exc}")

    if not previews and not errors:
        errors.append("no sendable recipients")

    if campaign.purpose == "marketing":
        if not campaign.consent_acknowledged:
            errors.append("marketing consent must be acknowledged")
        if not campaign.suppression_synced:
            errors.append("suppression synchronization is required")
        if not campaign.unsubscribe_base_url:
            errors.append("unsubscribe service is required")

    return {
        "ok": not errors,
        "errors": errors,
        "previews": previews,
        "excluded": len(recipients) - len(previews),
        "attachments": [{"name": a.filename, "size": a.size} for a in campaign.attachments],
    }


@router.post("/campaigns/{campaign_id}/preflight")
def run_preflight(campaign_id: str, db: Session = Depends(get_db)):
    return preflight(_campaign(db, campaign_id), db)


@router.get("/campaigns/{campaign_id}/preview/{recipient_id}")
def preview_recipient(campaign_id: str, recipient_id: str, db: Session = Depends(get_db)):
    campaign = _campaign(db, campaign_id)
    recipient = db.get(Recipient, recipient_id)
    if not recipient or recipient.campaign_id != campaign.id:
        raise HTTPException(404, "recipient not found in this campaign")
    values = dict(recipient.values)
    values.setdefault("email", recipient.email)
    missing_vars = validate_template_variables(campaign.subject_template, campaign.body_template, values)
    try:
        rendered = render_message(campaign.subject_template, campaign.body_template, campaign.body_mode, values)
        return {
            "recipient_id": recipient.id,
            "email": recipient.email,
            "subject": rendered.subject,
            "html": rendered.html,
            "text": rendered.text,
            "values": values,
            "missing_variables": missing_vars,
        }
    except Exception as exc:
        raise HTTPException(422, f"Failed to render message: {exc}") from exc


@router.post("/campaigns/{campaign_id}/suppression/sync")
def trigger_suppression_sync(campaign_id: str, db: Session = Depends(get_db)):
    campaign = _campaign(db, campaign_id)
    synced_count = sync_suppressions(db)
    campaign.suppression_synced = True
    db.commit()
    suppressed_count = db.scalar(
        select(func.count()).select_from(Recipient).where(Recipient.campaign_id == campaign.id, Recipient.suppressed)
    ) or 0
    return {"ok": True, "synced_events": synced_count, "campaign_suppressed_recipients": suppressed_count}


@router.post("/campaigns/{campaign_id}/test-email")
def send_test_email(campaign_id: str, data: TestEmailIn, db: Session = Depends(get_db)):
    campaign = _campaign(db, campaign_id)
    if not campaign.profile_id:
        raise HTTPException(400, "campaign does not have an assigned profile")
    profile = db.get(Profile, campaign.profile_id)
    if not profile:
        raise HTTPException(404, "sender profile not found")

    sample_recipient = None
    if data.sample_recipient_id:
        sample_recipient = db.get(Recipient, data.sample_recipient_id)
    if not sample_recipient:
        sample_recipient = db.scalar(
            select(Recipient).where(Recipient.campaign_id == campaign.id, Recipient.valid).order_by(Recipient.id)
        )

    values = dict(sample_recipient.values) if sample_recipient else {}
    values.setdefault("email", data.recipient_email)

    missing_vars = validate_template_variables(campaign.subject_template, campaign.body_template, values)
    if missing_vars:
        raise HTTPException(422, f"Missing required template variable(s): {', '.join(missing_vars)}")

    try:
        rendered = render_message(campaign.subject_template, campaign.body_template, campaign.body_mode, values)
        # Prefix subject with [TEST]
        test_rendered = type(rendered)(
            subject=f"[TEST] {rendered.subject}",
            html=rendered.html,
            text=rendered.text,
        )
        message = build_message(campaign, data.recipient_email, test_rendered, profile)
        client = connect(
            profile,
            password=get_secret(profile.id, "password"),
            access_token=get_secret(profile.id, "access_token"),
        )
        try:
            send(client, message)
        finally:
            try:
                client.quit()
            except Exception:
                pass
    except Exception as exc:
        raise HTTPException(502, f"Failed to send test email: {exc}") from exc

    return {"ok": True, "recipient_email": data.recipient_email, "subject": message["Subject"]}


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
    sent_24h = (
        db.scalar(
            select(func.count())
            .select_from(DeliveryAttempt)
            .where(
                DeliveryAttempt.outcome == "sent",
                DeliveryAttempt.attempted_at >= datetime.now(timezone.utc) - timedelta(hours=24),
            )
        )
        or 0
    )
    if sent_24h + count > profile.daily_cap and not data.confirm_guardrail_override:
        raise HTTPException(409, "rolling daily cap exceeded; explicit override confirmation required")
    campaign.guardrail_override = sent_24h + count > profile.daily_cap
    campaign.scheduled_at = data.scheduled_at.astimezone(timezone.utc)
    campaign.state = CampaignState.scheduled
    db.add(
        AuditLog(
            campaign_id=campaign.id,
            action="scheduled",
            detail={"at": campaign.scheduled_at.isoformat(), "guardrail_override": campaign.guardrail_override},
        )
    )
    db.commit()
    return {"state": campaign.state, "scheduled_at": campaign.scheduled_at}


@router.post("/campaigns/{campaign_id}/{action}")
def control(campaign_id: str, action: str, db: Session = Depends(get_db)):
    campaign = _campaign(db, campaign_id)
    transitions = {
        "pause": CampaignState.paused,
        "resume": CampaignState.scheduled,
        "cancel": CampaignState.cancelled,
        "confirm-overdue": CampaignState.scheduled,
    }
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
                counts = dict(
                    db.execute(
                        select(Recipient.status, func.count())
                        .where(Recipient.campaign_id == campaign_id)
                        .group_by(Recipient.status)
                    ).all()
                )
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
