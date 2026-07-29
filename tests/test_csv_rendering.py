import pytest
from jinja2 import UndefinedError

from mailmerge.csv_import import parse_csv
from mailmerge.rendering import render_message, valid_email


def test_csv_detects_semicolon_and_case_insensitive_duplicates():
    headers, rows = parse_csv(b"email;name\na@example.com;A\nA@EXAMPLE.COM;B\nbad;C\n", "email")
    assert headers == ["email", "name"]
    assert [row.valid for row in rows] == [True, False, False]
    assert rows[1].duplicate


def test_utf8_bom_is_supported():
    _, rows = parse_csv("\ufeffemail,name\na@example.com,Åsa\n".encode(), "email")
    assert rows[0].values["name"] == "Åsa"


def test_render_markdown_and_plain_text():
    result = render_message("Hello {{ name }}", "# Welcome\n\nHi **{{ name }}**", "markdown", {"name": "Ada"})
    assert result.subject == "Hello Ada"
    assert "<strong>Ada</strong>" in result.html
    assert "Hi Ada" in result.text


def test_missing_variable_only_raises_for_that_render():
    with pytest.raises(UndefinedError):
        render_message("Hi {{ missing }}", "Body", "markdown", {})


def test_sandbox_blocks_python_attribute_access():
    with pytest.raises(Exception):
        render_message("{{ ''.__class__.__mro__ }}", "Body", "markdown", {})


@pytest.mark.parametrize("value", ["a@b.example", "first.last+tag@example.com"])
def test_valid_email(value):
    assert valid_email(value)

