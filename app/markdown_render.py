"""Server-side markdown rendering with HTML sanitization."""

from __future__ import annotations

import bleach
import markdown

_ALLOWED_TAGS = [
    "p",
    "br",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "ul",
    "ol",
    "li",
    "blockquote",
    "pre",
    "code",
    "a",
    "strong",
    "em",
    "del",
    "table",
    "thead",
    "tbody",
    "tr",
    "th",
    "td",
    "hr",
]

_ALLOWED_ATTRIBUTES = {
    "a": ["href", "title", "rel"],
    "th": ["align"],
    "td": ["align"],
}

_ALLOWED_PROTOCOLS = ["http", "https", "mailto"]


def render_markdown(text: str) -> str:
    """Convert markdown to sanitized HTML safe for Jinja |safe."""
    if not text:
        return ""
    html = markdown.markdown(
        text,
        extensions=["fenced_code", "tables", "nl2br", "sane_lists"],
    )
    return bleach.clean(
        html,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRIBUTES,
        protocols=_ALLOWED_PROTOCOLS,
        strip=True,
    )
