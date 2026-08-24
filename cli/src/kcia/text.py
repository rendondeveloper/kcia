"""Whitespace normalization for free-text CLI input."""

from __future__ import annotations

import re


def normalize_text(text: str) -> str:
    """Strip surrounding whitespace and collapse internal runs to a single space."""
    return re.sub(r"\s+", " ", text).strip()
