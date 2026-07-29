from __future__ import annotations

import base64
import smtplib
import ssl
from typing import TYPE_CHECKING
from email.message import EmailMessage

if TYPE_CHECKING:
    from .models import Profile


class AuthenticationFailure(Exception):
    pass


def classify_smtp_error(exc: Exception) -> tuple[str, int | None]:
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return "authentication", exc.smtp_code
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        codes = [value[0] for value in exc.recipients.values()]
        return ("transient" if codes and all(400 <= code < 500 for code in codes) else "permanent"), (codes[0] if codes else None)
    if isinstance(exc, smtplib.SMTPResponseException):
        return ("transient" if 400 <= exc.smtp_code < 500 else "permanent"), exc.smtp_code
    if isinstance(exc, (TimeoutError, OSError, smtplib.SMTPServerDisconnected)):
        return "transient", None
    return "permanent", None


def connect(profile: "Profile", password: str | None = None, access_token: str | None = None):
    context = ssl.create_default_context()
    if not profile.verify_tls:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    if profile.security == "tls":
        client = smtplib.SMTP_SSL(profile.smtp_host, profile.smtp_port, timeout=30, context=context)
    else:
        client = smtplib.SMTP(profile.smtp_host, profile.smtp_port, timeout=30)
        client.ehlo()
        if profile.security == "starttls":
            client.starttls(context=context)
            client.ehlo()
    try:
        if profile.auth_type == "xoauth2":
            auth = f"user={profile.username}\x01auth=Bearer {access_token}\x01\x01"
            code, response = client.docmd("AUTH", "XOAUTH2 " + base64.b64encode(auth.encode()).decode())
            if code != 235:
                raise smtplib.SMTPAuthenticationError(code, response)
        elif profile.username:
            client.login(profile.username, password or "")
    except smtplib.SMTPAuthenticationError as exc:
        client.quit()
        raise AuthenticationFailure(str(exc)) from exc
    return client


def send(client, message: EmailMessage) -> None:
    client.send_message(message)
