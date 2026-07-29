from __future__ import annotations

import re
from dataclasses import dataclass
from email.utils import parseaddr

import html
try:
    import markdown as markdown_lib
except ImportError:  # minimal source-tree operation before optional dependencies are installed
    markdown_lib = None
try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None
from jinja2 import StrictUndefined
from jinja2.sandbox import SandboxedEnvironment


def _email(value: object) -> str:
    return str(value).strip().lower()


ENV = SandboxedEnvironment(undefined=StrictUndefined, autoescape=True)
ENV.filters.clear()
ENV.filters.update({
    "lower": lambda v: str(v).lower(),
    "upper": lambda v: str(v).upper(),
    "title": lambda v: str(v).title(),
    "trim": lambda v: str(v).strip(),
    "default": lambda v, d="": d if v is None else v,
    "email": _email,
})


@dataclass(frozen=True)
class RenderedMessage:
    subject: str
    html: str
    text: str


def valid_email(value: str) -> bool:
    _, address = parseaddr(value)
    return address == value.strip() and bool(re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", address))


def html_to_text(html: str) -> str:
    if BeautifulSoup:
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style"]):
            tag.decompose()
        text = soup.get_text(" ")
    else:
        text = re.sub(r"<[^>]+>", "\n", html)
        text = __import__("html").unescape(text)
    return "\n".join(re.sub(r"\s+", " ", line).strip() for line in text.splitlines() if line.strip())


def _markdown(source: str) -> str:
    if markdown_lib:
        return markdown_lib.markdown(source, extensions=["extra", "sane_lists"])
    escaped = html.escape(source)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
    lines = [f"<h1>{line[2:]}</h1>" if line.startswith("# ") else f"<p>{line}</p>" for line in escaped.splitlines() if line]
    return "\n".join(lines)


def render_message(subject_template: str, body_template: str, body_mode: str, values: dict) -> RenderedMessage:
    subject = ENV.from_string(subject_template).render(values)
    rendered = ENV.from_string(body_template).render(values)
    if "\r" in subject or "\n" in subject:
        raise ValueError("subject may not contain newlines")
    rendered_html = _markdown(rendered) if body_mode == "markdown" else rendered
    return RenderedMessage(subject=subject, html=rendered_html, text=html_to_text(rendered_html))
