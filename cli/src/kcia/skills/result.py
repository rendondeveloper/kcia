"""Evaluate skill runs and format user-facing result messages."""

from __future__ import annotations

import re
from dataclasses import dataclass

from kcia.waves.blocked import detect_blocked


def detect_skill_ok(output: str) -> bool:
    if not output:
        return False
    return bool(re.search(r"SKILL_OK\s*:", output, re.IGNORECASE))


@dataclass(frozen=True)
class SkillRunOutcome:
    exit_code: int
    output_text: str
    empty_output: bool = False
    missing_skill_ok: bool = False
    provider_failed: bool = False
    blocked_reason: str | None = None


def evaluate_skill_run(output_text: str, provider_exit_code: int) -> SkillRunOutcome:
    text = output_text or ""
    blocked = detect_blocked(text)
    if blocked:
        return SkillRunOutcome(
            exit_code=2,
            output_text=text,
            blocked_reason=blocked,
        )

    if provider_exit_code != 0:
        return SkillRunOutcome(
            exit_code=1,
            output_text=text,
            provider_failed=True,
            empty_output=not text.strip(),
            missing_skill_ok=bool(text.strip()) and not detect_skill_ok(text),
        )

    if not text.strip():
        return SkillRunOutcome(
            exit_code=1,
            output_text=text,
            empty_output=True,
        )

    if not detect_skill_ok(text):
        return SkillRunOutcome(
            exit_code=1,
            output_text=text,
            missing_skill_ok=True,
        )

    return SkillRunOutcome(exit_code=0, output_text=text)


def format_skill_run_messages(namespace: str, shortcut: str, outcome: SkillRunOutcome) -> list[str]:
    label = f"skill:{namespace}/{shortcut}"
    lines: list[str] = []

    if outcome.output_text.strip():
        lines.append(outcome.output_text.rstrip())
    elif outcome.empty_output:
        lines.append(f"{label} produced no output.")

    if outcome.missing_skill_ok:
        lines.append(
            f"{label} did not emit SKILL_OK: — treat this run as a failure."
        )

    if outcome.provider_failed and not outcome.empty_output and not outcome.missing_skill_ok:
        lines.append(f"{label} failed (provider exit non-zero).")

    return lines
