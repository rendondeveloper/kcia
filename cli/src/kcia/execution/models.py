"""Pure execution model objects.

These objects intentionally contain observable state only. They are safe to
persist in local KCIA metadata because they do not include secrets or hidden
provider reasoning.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class AgentTarget:
    provider: str
    model: str
    effort: str | None = None
    billing_profile: str | None = None
    account_key: str | None = None

    @property
    def id(self) -> str:
        return f"{self.provider}/{self.model}"


@dataclass(frozen=True)
class ExecutionAttempt:
    role: str
    target: AgentTarget
    operation_id: str
    attempt: int
    started_at: datetime
    finished_at: datetime | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    files_read: tuple[str, ...] = ()
    files_written: tuple[str, ...] = ()
    result_summary: str | None = None


@dataclass(frozen=True)
class HandoffRecord:
    objective: str
    criteria: tuple[str, ...] = ()
    approved_plan: str | None = None
    completed_steps: tuple[str, ...] = ()
    files_changed: tuple[str, ...] = ()
    validations: tuple[str, ...] = ()
    next_step: str | None = None
    pending_external_effects: tuple[str, ...] = ()
    workspace_epoch: str | None = None
    created_at: datetime | None = None
    attempts: tuple[ExecutionAttempt, ...] = field(default_factory=tuple)

    def prompt_delta(self) -> str:
        lines = [f"Objective: {self.objective}"]
        if self.criteria:
            lines.append("Acceptance criteria:")
            lines.extend(f"- {item}" for item in self.criteria)
        if self.approved_plan:
            lines.append(f"Approved plan: {self.approved_plan}")
        if self.completed_steps:
            lines.append("Completed steps:")
            lines.extend(f"- {item}" for item in self.completed_steps)
        if self.files_changed:
            lines.append("Files changed:")
            lines.extend(f"- {path}" for path in self.files_changed)
        if self.validations:
            lines.append("Validations:")
            lines.extend(f"- {item}" for item in self.validations)
        if self.pending_external_effects:
            lines.append("Pending external effects:")
            lines.extend(f"- {item}" for item in self.pending_external_effects)
        if self.next_step:
            lines.append(f"Next step: {self.next_step}")
        return "\n".join(lines)


def workspace_epoch(paths: list[Path]) -> str:
    """A cheap observable epoch for a set of paths.

    The full coordinator will store richer git and hash metadata. This helper is
    enough for pure tests and handoff records that need a deterministic marker.
    """
    parts = [str(path.resolve()) for path in paths]
    return "|".join(sorted(parts))
