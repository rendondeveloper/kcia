"""Detect that a wave declared itself unable to proceed.

A wave that stops and asks a question is not a failure and must not be treated
as success either: continuing past it means every later wave reasons on top of a
gap. Detection is deliberately narrow — a false positive halts a healthy run,
which is worse than missing one — so it keys on an explicit protocol marker plus
two exact shapes agents produced before that protocol existed.
"""

from __future__ import annotations

import re
from pathlib import Path

MAX_REASON_CHARS = 500
MAX_PREMATURE_BLOCK_RETRIES = 1

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


def should_honour_block(tool_calls: int, premature_retries: int) -> bool:
    """Whether a detected block should stop the wave.

    A ``BLOCKED:`` line with no tool use is retried once; only after that, or
    when the attempt made at least one tool call, is the block honoured.
    """
    if tool_calls > 0:
        return True
    return premature_retries >= MAX_PREMATURE_BLOCK_RETRIES


def format_premature_block_retry(reason: str, repo_root: Path) -> str:
    """Instruction injected when an agent blocks without using any tools."""
    plan_path = repo_root / ".ai" / "context" / "plan.md"
    section = _find_plan_section_for_reason(
        plan_path.read_text(encoding="utf-8") if plan_path.is_file() else "",
        reason,
    )
    lines = ["You reported BLOCKED without using any tools."]
    if section:
        lines.append(
            f"The plan already addresses this in **{section}** — read it and proceed."
        )
    else:
        lines.append(
            "No file was read. Read the approved plan and the affected code before "
            "reporting a blocker."
        )
    lines.append(f"Your reported question was: {reason}")
    return "\n".join(lines)


def _find_plan_section_for_reason(plan_text: str, reason: str) -> str | None:
    if not plan_text.strip():
        return None

    headings = re.findall(r"^#{2,3}\s+(.+)$", plan_text, re.MULTILINE)
    reason_tokens = {word for word in re.findall(r"\w+", reason.lower()) if len(word) > 3}

    best: str | None = None
    best_score = 0
    for title in headings:
        title_lower = title.lower()
        score = 0
        if "decision" in title_lower or "open question" in title_lower:
            score += 10
        title_tokens = {word for word in re.findall(r"\w+", title_lower) if len(word) > 3}
        score += len(reason_tokens & title_tokens)
        if score > best_score:
            best_score = score
            best = title
    return best if best_score > 0 else None
