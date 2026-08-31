from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .rendering import get_required_variables, valid_email


@dataclass(frozen=True)
class ImportedRecipient:
    email: str
    values: dict[str, Any]
    valid: bool
    error: str | None
    duplicate: bool
    missing_variables: list[str] = field(default_factory=list)


def parse_recipients_json(
    raw_data: bytes | str | list | dict,
    subject_template: str = "",
    body_template: str = "",
) -> tuple[list[str], list[ImportedRecipient]]:
    if isinstance(raw_data, bytes):
        text = raw_data.decode("utf-8-sig").strip()
    elif isinstance(raw_data, str):
        text = raw_data.strip()
    else:
        text = None

    if text is not None:
        if not text:
            raise ValueError("Input data is empty")
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            # Fallback: parse as JSONLines (NDJSON), one JSON object per non-empty line
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            if not lines:
                raise ValueError("Input data is empty")
            records = []
            for index, line in enumerate(lines):
                try:
                    line_obj = json.loads(line)
                    if isinstance(line_obj, dict):
                        records.append(line_obj)
                    elif isinstance(line_obj, list):
                        records.extend(line_obj)
                    else:
                        raise ValueError(f"Line {index + 1} is not a JSON object")
                except json.JSONDecodeError as exc:
                    raise ValueError(f"Invalid JSON/JSONLines syntax on line {index + 1}: {exc}") from exc
            parsed = records
    else:
        parsed = raw_data

    if isinstance(parsed, dict):
        if "recipients" in parsed and isinstance(parsed["recipients"], list):
            records = parsed["recipients"]
        elif "items" in parsed and isinstance(parsed["items"], list):
            records = parsed["items"]
        elif "data" in parsed and isinstance(parsed["data"], list):
            records = parsed["data"]
        else:
            records = [parsed]
    elif isinstance(parsed, list):
        records = parsed
    else:
        raise ValueError("JSON data must be a list of recipient objects, JSONLines records, or an object containing a 'recipients' list")

    required_vars = get_required_variables(subject_template, body_template)
    all_keys: set[str] = set()
    seen_normalized: set[str] = set()
    rows: list[ImportedRecipient] = []

    for index, record in enumerate(records):
        if not isinstance(record, dict):
            rows.append(
                ImportedRecipient(
                    email="",
                    values={},
                    valid=False,
                    error=f"Item at index {index} is not a JSON object",
                    duplicate=False,
                    missing_variables=[],
                )
            )
            continue

        # Extract email and values
        email_val = record.get("email") or record.get("Email") or record.get("recipient") or ""
        email = str(email_val).strip()
        normalized = email.casefold()

        # Extract values
        if "values" in record and isinstance(record["values"], dict):
            values = dict(record["values"])
            for k, v in record.items():
                if k not in {"values", "email", "Email", "recipient"} and k not in values:
                    values[k] = v
        else:
            values = {str(k): v for k, v in record.items()}

        values.setdefault("email", email)
        for k in values.keys():
            all_keys.add(k)

        # Check duplicate
        duplicate = normalized in seen_normalized if normalized else False
        if normalized:
            seen_normalized.add(normalized)

        email_is_valid = valid_email(email) and not duplicate

        # Check required template variables
        missing: list[str] = []
        if required_vars:
            for var in required_vars:
                val = values.get(var)
                if val is None or val == "":
                    missing.append(var)

        missing.sort()

        if duplicate:
            error = "duplicate email"
            valid = False
        elif not valid_email(email):
            error = "invalid email address"
            valid = False
        elif missing:
            error = f"missing required template variable(s): {', '.join(missing)}"
            valid = False
        else:
            error = None
            valid = True

        rows.append(
            ImportedRecipient(
                email=email,
                values=values,
                valid=valid,
                error=error,
                duplicate=duplicate,
                missing_variables=missing,
            )
        )

    return sorted(all_keys), rows
