from __future__ import annotations

import argparse
import smtplib
import sys
from datetime import datetime, timezone
from email.message import EmailMessage

from sqlalchemy import select

from .config import settings
from .db import SessionLocal, init_db
from .models import Profile
from .profile_config import load_profiles
from .rendering import valid_email
from .secrets import get_secret
from .smtp import AuthenticationFailure, classify_smtp_error, connect


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(description="Send one message to test a configured SMTP profile.")
    command.add_argument("--profile", required=True, help="Profile name from profiles.toml or the local database")
    command.add_argument("--to", required=True, dest="recipient", help="Recipient email address")
    command.add_argument("--from", required=True, dest="sender", help="Envelope and From email address")
    command.add_argument("--from-name", default="Mail Merge Test")
    command.add_argument("--subject", default="Local Mail Merge SMTP test")
    return command


def run() -> None:
    args = parser().parse_args()
    if not valid_email(args.recipient) or not valid_email(args.sender):
        parser().error("--to and --from must be valid email addresses")
    init_db()
    try:
        with SessionLocal() as db:
            if settings.profile_config:
                load_profiles(settings.profile_config, db)
            profile = db.scalar(select(Profile).where(Profile.name == args.profile))
            if profile is None:
                names = db.scalars(select(Profile.name).order_by(Profile.name)).all()
                available = ", ".join(names) if names else "none"
                raise RuntimeError(f"profile {args.profile!r} was not found (available: {available})")

            message = EmailMessage()
            message["From"] = f"{args.from_name} <{args.sender}>"
            message["To"] = args.recipient
            message["Subject"] = args.subject
            message.set_content(
                "This is a Local Mail Merge SMTP configuration test.\n\n"
                f"Profile: {profile.name}\n"
                f"SMTP server: {profile.smtp_host}:{profile.smtp_port}\n"
                f"Time (UTC): {datetime.now(timezone.utc).isoformat()}\n"
            )
            client = connect(
                profile,
                password=get_secret(profile.id, "password"),
                access_token=get_secret(profile.id, "access_token"),
            )
            try:
                refused = client.send_message(message, from_addr=args.sender, to_addrs=[args.recipient])
                if refused:
                    raise smtplib.SMTPRecipientsRefused(refused)
            finally:
                try:
                    client.quit()
                except Exception:
                    pass
            print(f"SMTP accepted the test message for {args.recipient} using profile {profile.name!r}.")
            print("Acceptance by SMTP does not guarantee final delivery; check the recipient mailbox and spam folder.")
    except AuthenticationFailure as exc:
        print(f"Authentication failed: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    except Exception as exc:
        kind, code = classify_smtp_error(exc)
        suffix = f" (SMTP {code})" if code else ""
        print(f"Test failed [{kind}]{suffix}: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    run()
