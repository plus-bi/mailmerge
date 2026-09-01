from __future__ import annotations

import mimetypes
import os
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from pathlib import Path

from .models import Campaign, Profile
from .rendering import RenderedMessage


def build_message(campaign: Campaign, recipient_email: str, rendered: RenderedMessage, profile: Profile | None = None) -> EmailMessage:
    message = EmailMessage()
    from_name = campaign.from_name or (profile.from_name if profile else "") or ""
    from_address = campaign.from_address or (profile.from_address if profile else "") or ""
    domain = from_address.split("@")[-1] if "@" in from_address else None
    message["Message-ID"] = make_msgid(domain=domain)
    message["From"] = formataddr((from_name, from_address))
    message["To"] = recipient_email
    message["Subject"] = rendered.subject
    reply_to = campaign.reply_to or (profile.reply_to if profile else None)
    if reply_to:
        message["Reply-To"] = reply_to

    # List-Unsubscribe Header
    include_unsubscribe = bool(
        campaign.list_unsubscribe_enabled
        or (profile and getattr(profile, "list_unsubscribe_one_click", False))
        or (campaign.purpose == "marketing")
        or (profile and getattr(profile, "list_unsubscribe", None))
    )
    if include_unsubscribe:
        raw_base = campaign.unsubscribe_base_url or (profile.list_unsubscribe if profile else None) or "https://mailmerge.plus.bi"
        unsubscribe = raw_base
        if raw_base.startswith("http") and "/u/" not in raw_base:
            secret = os.getenv("UNSUBSCRIBE_SIGNING_SECRET", "")
            if secret:
                from unsubscribe_service.main import sign_token
                token = sign_token(campaign.name, recipient_email, secret=secret)
                unsubscribe = f"{raw_base.rstrip('/')}/u/{token}"
        message["List-Unsubscribe"] = unsubscribe if unsubscribe.startswith("<") else f"<{unsubscribe}>"
        if unsubscribe.strip("<>").startswith("https://"):
            message["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    message.set_content(rendered.text)
    message.add_alternative(rendered.html, subtype="html")
    for attachment in campaign.attachments:
        path = Path(attachment.path)
        maintype, subtype = (attachment.content_type or mimetypes.guess_type(path.name)[0] or "application/octet-stream").split("/", 1)
        message.add_attachment(path.read_bytes(), maintype=maintype, subtype=subtype, filename=attachment.filename)
    return message
