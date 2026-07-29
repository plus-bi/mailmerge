from __future__ import annotations

import csv
import io
from dataclasses import dataclass

from .rendering import valid_email


@dataclass(frozen=True)
class ImportedRow:
    email: str
    values: dict[str, str]
    valid: bool
    error: str | None
    duplicate: bool


def detect_dialect(data: str) -> csv.Dialect:
    try:
        return csv.Sniffer().sniff(data[:8192], delimiters=",;\t|")
    except csv.Error:
        return csv.excel


def parse_csv(raw: bytes, email_column: str) -> tuple[list[str], list[ImportedRow]]:
    text = raw.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text), dialect=detect_dialect(text))
    if not reader.fieldnames or email_column not in reader.fieldnames:
        raise ValueError("email column is missing")
    seen: set[str] = set()
    rows: list[ImportedRow] = []
    for row in reader:
        values = {str(k): (v or "").strip() for k, v in row.items() if k is not None}
        email = values.get(email_column, "").strip()
        normalized = email.casefold()
        duplicate = normalized in seen
        if normalized:
            seen.add(normalized)
        valid = valid_email(email) and not duplicate
        error = "duplicate email" if duplicate else (None if valid else "invalid email")
        rows.append(ImportedRow(email, values, valid, error, duplicate))
    return list(reader.fieldnames), rows

