"""Detect that a wave declared itself unable to proceed.

A wave that stops and asks a question is not a failure and must not be treated
as success either: continuing past it means every later wave reasons on top of a
gap. Detection is deliberately narrow — a false positive halts a healthy run,
which is worse than missing one — so it keys on an explicit protocol marker plus
two exact shapes agents produced before that protocol existed.
"""

from __future__ import annotations

import re

MAX_REASON_CHARS = 500

# The protocol asked for in `_blocked.md.j2`. Leading markdown decoration is
# tolerated because models routinely bold or bullet the line.
_MARKER = re.compile(
    r"^[\s>*#\-]*(?:\*\*)?BLOCKED(?:\*\*)?\s*:\s*(?P<reason>.+)$",
    re.IGNORECASE | re.MULTILINE,
)

# Pre-protocol shapes, matched whole-line so a passing mention does not trigger.
_LEGACY = (
    re.compile(
        r"^[\s>*#\-]*(?:\*\*)?Open questions?\s*\(blocking\)(?:\*\*)?\s*:?\s*(?P<reason>.*)$",
        re.IGNORECASE | re.MULTILINE,
    ),
    # `**Status:**` puts the colon inside the emphasis, so it may fall either side.
    re.compile(
        r"^[\s>*#\-]*(?:\*\*)?Status\s*:?\s*(?:\*\*)?\s*:?\s*`?UNKNOWN`?"
        r"\s*(?:—|-|–)?\s*(?P<reason>.*)$",
        re.IGNORECASE | re.MULTILINE,
    ),
)


def detect_blocked(output: str) -> str | None:
    """Return the reason a wave is blocked, or None when it is not."""
    if not output:
        return None

    match = _MARKER.search(output)
    if match:
        return _clean(match.group("reason"))

    for pattern in _LEGACY:
        match = pattern.search(output)
        if match:
            return _clean(match.group("reason")) or "the agent reported it cannot proceed"
    return None


def _clean(reason: str) -> str:
    reason = reason.strip().strip("*").strip()
    if len(reason) > MAX_REASON_CHARS:
        reason = reason[: MAX_REASON_CHARS - 1].rstrip() + "…"
    return reason
