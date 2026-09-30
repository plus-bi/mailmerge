#!/usr/bin/env python3
"""Export matching Gmail messages as original .eml files.

At runtime the service-account JSON is read from Google Secret Manager. The
process uses Application Default Credentials (ADC) to access that secret, then
uses the delegated service account for Gmail read-only access.
"""

from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
from email.utils import parsedate_to_datetime
import json
from pathlib import Path
import sys
from typing import Any


DEFAULT_SECRET_PROJECT = "plusbi"
DEFAULT_SECRET_ID = "gmail-export-service-account-key"
DEFAULT_MAILBOX = "haris@plus.bi"
DEFAULT_SUBJECT_PHRASE = "kundene deres mer enn bare tall"
CLOUD_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
GMAIL_READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Export messages delivered to a Gmail mailbox whose subject "
            "contains a phrase. The Gmail service-account key is read from "
            "Google Secret Manager."
        )
    )
    parser.add_argument(
        "--secret-project",
        default=DEFAULT_SECRET_PROJECT,
        help=f"GCP project containing the key secret (default: {DEFAULT_SECRET_PROJECT})",
    )
    parser.add_argument(
        "--secret-id",
        default=DEFAULT_SECRET_ID,
        help=f"Secret Manager secret ID (default: {DEFAULT_SECRET_ID})",
    )
    parser.add_argument(
        "--mailbox",
        default=DEFAULT_MAILBOX,
        help=f"Mailbox to search as (default: {DEFAULT_MAILBOX})",
    )
    parser.add_argument(
        "--recipient",
        default=DEFAULT_MAILBOX,
        help=f"Address Gmail should report as delivered-to (default: {DEFAULT_MAILBOX})",
    )
    parser.add_argument(
        "--subject-phrase",
        default=DEFAULT_SUBJECT_PHRASE,
        help=f"Phrase required in the subject (default: {DEFAULT_SUBJECT_PHRASE!r})",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("gmail-export"),
        help="Directory for .eml files and manifest.json (default: ./gmail-export)",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Re-download all matches instead of reusing saved messages.",
    )
    parser.add_argument(
        "--store-key-from",
        type=Path,
        metavar="JSON_FILE",
        help="Bootstrap mode: upload this service-account JSON as a new secret version, then exit.",
    )
    parser.add_argument(
        "--bootstrap-credentials",
        type=Path,
        metavar="JSON_FILE",
        help=(
            "Service-account key used only to authenticate the bootstrap upload. "
            "If omitted, bootstrap uses ADC."
        ),
    )
    return parser.parse_args()


def google_api_modules():
    try:
        import google.auth
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise RuntimeError(
            "Missing Google API dependencies. Install with: "
            "python -m pip install -e '.[gmail-export]'"
        ) from exc
    return google.auth, service_account, build


def adc_credentials():
    google_auth, _, _ = google_api_modules()
    credentials, _ = google_auth.default(scopes=[CLOUD_SCOPE])
    return credentials


def secret_manager_service(credentials):
    _, _, build = google_api_modules()
    return build("secretmanager", "v1", credentials=credentials, cache_discovery=False)


def secret_resource(secret_project: str, secret_id: str) -> str:
    return f"projects/{secret_project}/secrets/{secret_id}"


def store_key_secret(args: argparse.Namespace) -> int:
    """Upload a local key once; existing secret versions remain recoverable."""
    key_path = args.store_key_from.expanduser()
    if not key_path.is_file():
        print(f"Service-account key file not found: {key_path}", file=sys.stderr)
        return 2
    if args.bootstrap_credentials:
        _, service_account, _ = google_api_modules()
        bootstrap_path = args.bootstrap_credentials.expanduser()
        if not bootstrap_path.is_file():
            print(f"Bootstrap credential file not found: {bootstrap_path}", file=sys.stderr)
            return 2
        credentials = service_account.Credentials.from_service_account_file(
            str(bootstrap_path), scopes=[CLOUD_SCOPE]
        )
    else:
        credentials = adc_credentials()

    service = secret_manager_service(credentials)
    parent = secret_resource(args.secret_project, args.secret_id)
    secret_bytes = key_path.read_bytes()
    # Validate the local file before storing it, without printing any key data.
    key_info = json.loads(secret_bytes)
    if key_info.get("type") != "service_account" or not key_info.get("private_key"):
        raise ValueError("The supplied JSON does not look like a service-account key.")

    from googleapiclient.errors import HttpError

    secret_exists = False
    try:
        service.projects().secrets().get(name=parent).execute()
        secret_exists = True
    except HttpError as exc:
        if exc.resp.status != 404:
            raise
        service.projects().secrets().create(
            parent=f"projects/{args.secret_project}",
            secretId=args.secret_id,
            body={"replication": {"automatic": {}}},
        ).execute()

    if secret_exists:
        try:
            latest = service.projects().secrets().versions().access(
                name=f"{parent}/versions/latest"
            ).execute()
            latest_bytes = base64.b64decode(latest["payload"]["data"])
            if latest_bytes == secret_bytes:
                print(f"The same key is already stored in {parent}; no new version added.")
                return 0
        except HttpError as exc:
            # A secret can exist without an enabled version yet. In that case,
            # proceed to add the first version; re-raise other access failures.
            if exc.resp.status != 404:
                raise

    version = service.projects().secrets().addVersion(
        parent=parent,
        body={
            "payload": {
                "data": base64.b64encode(secret_bytes).decode("ascii"),
            }
        },
    ).execute()
    print(f"Stored service-account key as {version.get('name', 'a new secret version')}")
    print("The exporter will read the latest version at runtime.")
    return 0


def gmail_query(recipient: str, subject_phrase: str) -> str:
    # to: matches the requested address and does not include mail addressed to
    # a different alias that was merely delivered into this mailbox.
    escaped_phrase = subject_phrase.replace("\\", "\\\\").replace('"', '\\"')
    return f'to:{recipient} subject:"{escaped_phrase}"'


def decoded_raw_message(encoded: str) -> bytes:
    padding = "=" * (-len(encoded) % 4)
    return base64.urlsafe_b64decode(encoded + padding)


def safe_date_prefix(date_header: str | None) -> str:
    if date_header:
        try:
            date = parsedate_to_datetime(date_header)
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
            return date.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        except (TypeError, ValueError, OverflowError):
            pass
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def header_value(message: Any, name: str) -> str:
    value = message.get(name)
    return str(value) if value is not None else ""


def fetch_service_account_info(args: argparse.Namespace) -> dict[str, Any]:
    credentials = adc_credentials()
    service = secret_manager_service(credentials)
    secret_version = (
        f"{secret_resource(args.secret_project, args.secret_id)}/versions/latest"
    )
    response = service.projects().secrets().versions().access(
        name=secret_version
    ).execute()
    key_bytes = base64.b64decode(response["payload"]["data"])
    info = json.loads(key_bytes)
    if info.get("type") != "service_account" or not info.get("private_key"):
        raise ValueError("The selected Secret Manager version is not a service-account key.")
    return info


def export_messages(args: argparse.Namespace) -> int:
    _, service_account, build = google_api_modules()
    key_info = fetch_service_account_info(args)
    credentials = service_account.Credentials.from_service_account_info(
        key_info,
        scopes=[GMAIL_READONLY_SCOPE],
        subject=args.mailbox,
    )
    service = build("gmail", "v1", credentials=credentials, cache_discovery=False)
    query = gmail_query(args.recipient, args.subject_phrase)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = args.output_dir / "manifest.json"
    previous_by_id: dict[str, dict[str, Any]] = {}
    if manifest_path.is_file():
        try:
            previous = json.loads(manifest_path.read_text(encoding="utf-8"))
            previous_by_id = {
                item["gmail_message_id"]: item
                for item in previous.get("messages", [])
                if isinstance(item, dict)
                and isinstance(item.get("gmail_message_id"), str)
            }
        except (OSError, ValueError, TypeError):
            print("Warning: could not read the prior manifest; messages will be re-downloaded.", file=sys.stderr)

    exported: list[dict[str, Any]] = []
    downloaded_count = 0
    reused_count = 0
    page_token: str | None = None

    while True:
        result = (
            service.users()
            .messages()
            .list(
                userId="me",
                q=query,
                maxResults=500,
                pageToken=page_token,
            )
            .execute()
        )
        for match in result.get("messages", []):
            message_id = match["id"]
            previous_item = previous_by_id.get(message_id)
            if not args.refresh and previous_item:
                previous_filename = previous_item.get("file")
                if isinstance(previous_filename, str):
                    previous_path = args.output_dir / previous_filename
                    if (
                        previous_path.parent == args.output_dir
                        and previous_path.suffix == ".eml"
                        and previous_path.is_file()
                    ):
                        exported.append(previous_item)
                        reused_count += 1
                        continue

            gmail_message = (
                service.users()
                .messages()
                .get(userId="me", id=message_id, format="raw")
                .execute()
            )
            raw = decoded_raw_message(gmail_message["raw"])
            parsed = BytesParser(policy=policy.default).parsebytes(raw)

            message_id = gmail_message["id"]
            filename = f"{safe_date_prefix(header_value(parsed, 'Date'))}_{message_id}.eml"
            (args.output_dir / filename).write_bytes(raw)
            downloaded_count += 1
            exported.append(
                {
                    "gmail_message_id": message_id,
                    "thread_id": gmail_message.get("threadId", ""),
                    "file": filename,
                    "date": header_value(parsed, "Date"),
                    "from": header_value(parsed, "From"),
                    "to": header_value(parsed, "To"),
                    "subject": header_value(parsed, "Subject"),
                    "label_ids": gmail_message.get("labelIds", []),
                }
            )

        page_token = result.get("nextPageToken")
        if not page_token:
            break

    current_files = {item["file"] for item in exported}
    stale_removed_count = 0
    if manifest_path.is_file():
        try:
            previous = json.loads(manifest_path.read_text(encoding="utf-8"))
            previous_files = {
                item["file"]
                for item in previous.get("messages", [])
                if isinstance(item, dict) and isinstance(item.get("file"), str)
            }
            for stale_filename in previous_files - current_files:
                stale_path = args.output_dir / stale_filename
                if stale_path.parent == args.output_dir and stale_path.suffix == ".eml":
                    stale_removed_count += int(stale_path.exists())
                    stale_path.unlink(missing_ok=True)
        except (OSError, ValueError, TypeError):
            print("Warning: could not clean stale files listed in the old manifest.", file=sys.stderr)

    manifest = {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "mailbox": args.mailbox,
        "recipient_filter": args.recipient,
        "subject_phrase": args.subject_phrase,
        "gmail_query": query,
        "exported_count": len(exported),
        "downloaded_count": downloaded_count,
        "reused_count": reused_count,
        "stale_removed_count": stale_removed_count,
        "messages": exported,
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        f"Saved {len(exported)} matching messages to {args.output_dir.resolve()} "
        f"({downloaded_count} downloaded, {reused_count} reused, "
        f"{stale_removed_count} stale files removed)."
    )
    print(f"Manifest: {manifest_path.resolve()}")
    return 0


def main() -> int:
    args = parse_args()
    args.output_dir = args.output_dir.expanduser()
    try:
        if args.bootstrap_credentials and not args.store_key_from:
            print("--bootstrap-credentials requires --store-key-from", file=sys.stderr)
            return 2
        if args.store_key_from:
            return store_key_secret(args)
        return export_messages(args)
    except Exception as exc:
        # Do not print secret payloads or credential data.
        print(f"Gmail export failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
