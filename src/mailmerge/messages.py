from __future__ import annotations

import mimetypes
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from pathlib import Path

from .models import Campaign, Profile
from .rendering import RenderedMessage


def build_message(campaign: Campaign, recipient_email: str, rendered: RenderedMessage, profile: Profile | None = None) -> EmailMessage:
    message = EmailMessage()
    message["Message-ID"] = make_msgid(domain=campaign.from_address.split("@")[-1] or None)
    message["From"] = formataddr((campaign.from_name, campaign.from_address))
    message["To"] = recipient_email
    message["Subject"] = rendered.subject
    reply_to = campaign.reply_to or (profile.reply_to if profile else None)
    if reply_to:
        message["Reply-To"] = reply_to
    unsubscribe = profile.list_unsubscribe if profile and profile.list_unsubscribe else campaign.unsubscribe_base_url
    if unsubscribe:
        message["List-Unsubscribe"] = unsubscribe if unsubscribe.startswith("<") else f"<{unsubscribe}>"
    if (profile and profile.list_unsubscribe_one_click) or (campaign.purpose == "marketing" and campaign.unsubscribe_base_url):
        message["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    message.set_content(rendered.text)
    message.add_alternative(rendered.html, subtype="html")
    for attachment in campaign.attachments:
        path = Path(attachment.path)
        maintype, subtype = (attachment.content_type or mimetypes.guess_type(path.name)[0] or "application/octet-stream").split("/", 1)
        message.add_attachment(path.read_bytes(), maintype=maintype, subtype=subtype, filename=attachment.filename)
    return message
