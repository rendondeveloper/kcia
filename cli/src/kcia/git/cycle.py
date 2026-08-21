"""Work-cycle state: one feature branch until `kcia done` closes it.

Under git flow, kcia keeps a single open cycle per repository. Every `kcia work`
while the cycle is open stays on that branch — even a new task prompt — until
`kcia done` or `kcia work abort` closes it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

CYCLE_SCHEMA_VERSION = 1


def cycle_path(repo_root: Path) -> Path:
    return repo_root / ".ai" / "local" / "cycle.json"


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class WorkCycle:
    branch: str
    base_branch: str
    opened_at: str
    task_id: str | None = None


def load_cycle(repo_root: Path) -> WorkCycle | None:
    path = cycle_path(repo_root)
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return None
    branch = data.get("branch")
    if not isinstance(branch, str) or not branch:
        return None
    base = data.get("base_branch")
    opened = data.get("opened_at")
    task_id = data.get("task_id")
    return WorkCycle(
        branch=branch,
        base_branch=base if isinstance(base, str) else "",
        opened_at=opened if isinstance(opened, str) else "",
        task_id=task_id if isinstance(task_id, str) else None,
    )


def is_cycle_open(repo_root: Path) -> bool:
    return load_cycle(repo_root) is not None


def open_cycle(
    repo_root: Path,
    branch: str,
    base_branch: str,
    *,
    task_id: str | None = None,
) -> None:
    path = cycle_path(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": CYCLE_SCHEMA_VERSION,
                "branch": branch,
                "base_branch": base_branch,
                "opened_at": _now_iso(),
                "task_id": task_id,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def close_cycle(repo_root: Path) -> bool:
    path = cycle_path(repo_root)
    if not path.is_file():
        return False
    path.unlink()
    return True
