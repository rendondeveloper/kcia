"""Whitespace normalization for free-text CLI input."""

from __future__ import annotations


def normalize_text(text: str) -> str:
    """Strip surrounding whitespace and normalize line endings to ``\\n``.

    Internal whitespace, blank lines, and Markdown structure are left intact so
    multi-line specs stay readable after they are stored as task/prompt text.
    """
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()
