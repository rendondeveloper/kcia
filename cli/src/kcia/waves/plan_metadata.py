"""Parse the canonical metadata block out of `.ai/context/plan.md`.

The `analysis` wave writes a fenced YAML block with `type`/`ticket`/`title`/
`summary`/`affected_files`/`acceptance_criteria` so that branch names, commit
subjects, commit types, and session-history entries all derive from what the
plan decided rather than from the raw task prompt. This module is the single
place that reads that block; every consumer (autobranch, `kcia branch start`,
`kcia done`) goes through :func:`load` and falls back to the raw prompt/title
itself when there is no plan yet, or the plan has no metadata block.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

_FENCED_YAML_RE = re.compile(r"```yaml\s*([\s\S]*?)```", re.MULTILINE)


@dataclass(frozen=True)
class PlanMetadata:
    """The plan's decided facts about the work — the source for derived metadata."""

    type: str | None = None
    ticket: str | None = None
    title: str | None = None
    summary: str | None = None
    affected_files: dict[str, list[str]] = field(default_factory=dict)
    acceptance_criteria: list[str] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)


def _first_metadata_block(plan_text: str) -> dict | None:
    """The first fenced YAML block that looks like plan metadata (has `title`).

    `execution.profiles[]` (see `plan_execution.py`) is a separate fenced block
    for a different purpose; skipping blocks without `title` keeps the two
    parsers independent without needing to merge their schemas.
    """
    for match in _FENCED_YAML_RE.finditer(plan_text):
        payload = (match.group(1) or "").strip()
        if not payload:
            continue
        try:
            data = yaml.safe_load(payload)
        except yaml.YAMLError:
            continue
        if isinstance(data, dict) and "title" in data:
            return data
    return None


def _str_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _affected_files(value: object) -> dict[str, list[str]]:
    if not isinstance(value, dict):
        return {}
    return {
        key: _str_list(value.get(key))
        for key in ("modify", "create", "delete", "tests")
        if key in value
    }


def parse_plan_metadata(plan_text: str) -> PlanMetadata | None:
    """Parse the metadata block from `plan.md` content, or None if there isn't one."""
    if not plan_text.strip():
        return None
    data = _first_metadata_block(plan_text)
    if data is None:
        return None

    title = data.get("title")
    return PlanMetadata(
        type=str(data["type"]).strip() if data.get("type") else None,
        ticket=str(data["ticket"]).strip() if data.get("ticket") else None,
        title=str(title).strip() if title else None,
        summary=str(data["summary"]).strip() if data.get("summary") else None,
        affected_files=_affected_files(data.get("affected_files")),
        acceptance_criteria=_str_list(data.get("acceptance_criteria")),
    )


def _parse_decisions(decisions_text: str) -> list[str]:
    """Confirmed decisions as a flat list, one per markdown bullet."""
    decisions: list[str] = []
    for line in decisions_text.splitlines():
        stripped = line.strip()
        if stripped.startswith(("- ", "* ")):
            item = stripped[2:].strip()
            if item:
                decisions.append(item)
    return decisions


def load(repo_root: Path) -> PlanMetadata | None:
    """Load plan metadata for `repo_root`, merging in `decisions.md` when present.

    Returns None (never a partially-filled object) when `.ai/context/plan.md`
    does not exist or carries no metadata block, so callers can tell "no plan
    yet" apart from "plan with nothing set" and fall back accordingly.
    """
    plan_path = repo_root / ".ai" / "context" / "plan.md"
    if not plan_path.is_file():
        return None

    metadata = parse_plan_metadata(plan_path.read_text(encoding="utf-8"))
    if metadata is None:
        return None

    decisions_path = repo_root / ".ai" / "context" / "decisions.md"
    decisions: list[str] = []
    if decisions_path.is_file():
        decisions = _parse_decisions(decisions_path.read_text(encoding="utf-8"))

    if decisions:
        metadata = PlanMetadata(
            type=metadata.type,
            ticket=metadata.ticket,
            title=metadata.title,
            summary=metadata.summary,
            affected_files=metadata.affected_files,
            acceptance_criteria=metadata.acceptance_criteria,
            decisions=decisions,
        )
    return metadata
