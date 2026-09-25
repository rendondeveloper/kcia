"""Local handoff records for provider switches."""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from kcia.execution.models import HandoffRecord


class HandoffStore:
    def __init__(self, repo_root: Path) -> None:
        self.root = repo_root / ".ai" / "local" / "handoffs"

    def save(self, operation_id: str, record: HandoffRecord) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f"{operation_id}.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(_record_to_json(record), indent=2, sort_keys=True),
            encoding="utf-8",
        )
        os.replace(tmp, path)
        return path

    def load(self, operation_id: str) -> HandoffRecord | None:
        path = self.root / f"{operation_id}.json"
        if not path.is_file():
            return None
        return _record_from_json(json.loads(path.read_text(encoding="utf-8")))


def _record_to_json(record: HandoffRecord) -> dict:
    data = asdict(record)
    if data["created_at"] is not None:
        data["created_at"] = data["created_at"].isoformat()
    for attempt in data["attempts"]:
        attempt["started_at"] = attempt["started_at"].isoformat()
        if attempt["finished_at"] is not None:
            attempt["finished_at"] = attempt["finished_at"].isoformat()
    return data


def _record_from_json(data: dict) -> HandoffRecord:
    created_at = data.get("created_at")
    if created_at:
        data["created_at"] = datetime.fromisoformat(created_at)
    else:
        data["created_at"] = datetime.now(UTC)
    data["criteria"] = tuple(data.get("criteria") or [])
    data["completed_steps"] = tuple(data.get("completed_steps") or [])
    data["files_changed"] = tuple(data.get("files_changed") or [])
    data["validations"] = tuple(data.get("validations") or [])
    data["pending_external_effects"] = tuple(data.get("pending_external_effects") or [])
    data["attempts"] = tuple()
    return HandoffRecord(**data)
