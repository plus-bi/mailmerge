from datetime import datetime, timezone
import pytest
from jinja2 import UndefinedError

from mailmerge.json_import import parse_recipients_json
from mailmerge.rendering import (
    extract_variables,
    get_required_variables,
    render_message,
    templates_for_unsubscribe_setting,
    valid_email,
    validate_template_variables,
)
from mailmerge.worker import is_within_working_hours
from mailmerge.models import Campaign, Profile


def test_json_import_flat_records_and_deduplication():
    raw = [
        {"email": "a@example.com", "name": "Alice", "role": "Eng"},
        {"email": "A@EXAMPLE.COM", "name": "Duplicate Alice", "role": "Eng"},
        {"email": "invalid-email", "name": "Bad"},
    ]
    keys, rows = parse_recipients_json(raw)
    assert "name" in keys
    assert "role" in keys
    assert len(rows) == 3
    assert rows[0].valid is True
    assert rows[0].email == "a@example.com"
    assert rows[0].values["name"] == "Alice"
    assert rows[1].valid is False
    assert rows[1].duplicate is True
    assert rows[2].valid is False
    assert rows[2].error == "invalid email address"


def test_json_import_nested_values_and_wrapper_dict():
    raw = {
        "recipients": [
            {"email": "bob@example.com", "values": {"company": "Acme", "plan": "Pro"}},
            {"email": "carol@example.com", "values": {"company": "Globex", "plan": "Enterprise"}},
        ]
    }
    keys, rows = parse_recipients_json(raw)
    assert "company" in keys
    assert "plan" in keys
    assert len(rows) == 2
    assert rows[0].valid is True
    assert rows[0].values["company"] == "Acme"
    assert rows[1].values["plan"] == "Enterprise"


def test_template_variable_extraction_and_validation():
    subject_tmpl = "Hello {{ first_name }}, update on {{ project_name }}"
    body_tmpl = "Hi {{ first_name }}, your role at {{ company }} is active."
    required = get_required_variables(subject_tmpl, body_tmpl)
    assert required == {"first_name", "project_name", "company"}

    # Missing project_name and company
    missing = validate_template_variables(subject_tmpl, body_tmpl, {"first_name": "Ada"})
    assert set(missing) == {"project_name", "company"}

    # All provided
    complete = validate_template_variables(
        subject_tmpl,
        body_tmpl,
        {"first_name": "Ada", "project_name": "Apollo", "company": "NASA"},
    )
    assert complete == []


def test_json_import_with_template_variable_validation():
    subject_tmpl = "Invitation for {{ first_name }}"
    body_tmpl = "Welcome to {{ company }}!"

    raw = [
        {"email": "valid@example.com", "first_name": "Alice", "company": "Acme"},
        {"email": "missing@example.com", "first_name": "Bob"},  # missing company
    ]
    _, rows = parse_recipients_json(raw, subject_template=subject_tmpl, body_template=body_tmpl)
    assert rows[0].valid is True
    assert rows[0].error is None

    assert rows[1].valid is False
    assert "missing required template variable(s): company" in rows[1].error
    assert rows[1].missing_variables == ["company"]


def test_render_markdown_and_plain_text():
    result = render_message("Hello {{ name }}", "# Welcome\n\nHi **{{ name }}**", "markdown", {"name": "Ada"})
    assert result.subject == "Hello Ada"
    assert "<strong>Ada</strong>" in result.html
    assert "Hi Ada" in result.text


def test_missing_variable_in_strict_undefined_raises():
    with pytest.raises(UndefinedError):
        render_message("Hi {{ missing }}", "Body", "markdown", {})


def test_disabled_unsubscribe_removes_entire_template_lines():
    subject, body = templates_for_unsubscribe_setting(
        "Newsletter",
        "Hello\n\nIf you prefer not to receive these emails, you can [unsubscribe here]({{ unsubscribe_url }}).\n\nRegards\n",
        enabled=False,
    )

    assert subject == "Newsletter"
    assert "unsubscribe_url" not in body
    assert "If you prefer" not in body
    assert "Hello" in body
    assert "Regards" in body
    assert render_message(subject, body, "markdown", {}).subject == "Newsletter"


def test_enabled_unsubscribe_preserves_templates():
    templates = ("Newsletter", "Unsubscribe: {{ unsubscribe_url }}")
    assert templates_for_unsubscribe_setting(*templates, enabled=True) == templates


def test_working_hours_guardrail():
    campaign = Campaign(
        name="Test",
        working_hours_enabled=True,
        working_hours_start=9,
        working_hours_end=17,
        working_hours_timezone="UTC",
    )
    # Wednesday at 10:00 UTC -> inside
    wed_10am = datetime(2026, 8, 26, 10, 0, 0, tzinfo=timezone.utc)
    assert is_within_working_hours(campaign, None, now_utc=wed_10am) is True

    # Wednesday at 20:00 UTC -> outside
    wed_8pm = datetime(2026, 8, 26, 20, 0, 0, tzinfo=timezone.utc)
    assert is_within_working_hours(campaign, None, now_utc=wed_8pm) is False

    # Saturday at 12:00 UTC -> weekend outside
    sat_noon = datetime(2026, 8, 29, 12, 0, 0, tzinfo=timezone.utc)
    assert is_within_working_hours(campaign, None, now_utc=sat_noon) is False


@pytest.mark.parametrize("value", ["a@b.example", "first.last+tag@example.com"])
def test_valid_email(value):
    assert valid_email(value)


def test_jsonlines_import_string_and_bytes():
    jsonl_str = (
        '{"email": "alice@example.com", "name": "Alice", "team": "Platform"}\n'
        '{"email": "bob@example.com", "name": "Bob", "team": "Data"}\n'
        '\n'  # empty line should be ignored
        '{"email": "charlie@example.com", "name": "Charlie", "team": "Security"}\n'
    )
    keys, rows = parse_recipients_json(jsonl_str)
    assert set(keys) == {"email", "name", "team"}
    assert len(rows) == 3
    assert all(r.valid for r in rows)
    assert rows[0].email == "alice@example.com"
    assert rows[0].values["team"] == "Platform"
    assert rows[1].email == "bob@example.com"
    assert rows[2].email == "charlie@example.com"

    # Also test bytes with utf-8-sig
    keys_b, rows_b = parse_recipients_json(jsonl_str.encode("utf-8-sig"))
    assert len(rows_b) == 3
    assert rows_b[0].email == "alice@example.com"


def test_jsonlines_invalid_syntax_raises():
    invalid_jsonl = '{"email": "valid@example.com"}\n{not valid json}'
    with pytest.raises(ValueError, match="Invalid JSON/JSONLines syntax"):
        parse_recipients_json(invalid_jsonl)
