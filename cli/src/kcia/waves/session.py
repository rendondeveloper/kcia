"""Task session persistence, locking, and input classification."""

from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import yaml

from kcia.git.cycle import close_cycle, load_cycle
from kcia.profiles.schema import Manifest

TaskMode = Literal["ticket", "prompt"]
WaveStatus = Literal[
    "pending",
    "running",
    "completed",
    "failed",
    "skipped",
    "awaiting_input",
    "awaiting_manual_completion",
]

SESSION_SCHEMA_VERSION = 2


def session_path(repo_root: Path) -> Path:
    return repo_root / ".ai" / "local" / "session.json"


def manifest_path(repo_root: Path) -> Path:
    return repo_root / ".ai" / "manifest.yaml"


def context_dir(repo_root: Path) -> Path:
    return repo_root / ".ai" / "context"


def runs_dir(repo_root: Path) -> Path:
    return repo_root / ".ai" / "local" / "runs"


def load_manifest(repo_root: Path) -> Manifest | None:
    path = manifest_path(repo_root)
    if not path.is_file():
        return None
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return Manifest.model_validate(data)


def load_manifest_raw(repo_root: Path) -> dict[str, Any]:
    path = manifest_path(repo_root)
    if not path.is_file():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def classify_input(
    text: str,
    manifest: dict[str, Any] | None,
    *,
    ticket: bool = False,
    prompt: bool = False,
    issue_tracker_connected: bool = False,
) -> TaskMode:
    if ticket:
        return "ticket"
    if prompt:
        return "prompt"

    manifest = manifest or {}
    integrations = manifest.get("integrations") or {}
    jira = integrations.get("jira") or {}
    # Enabling the Atlassian MCP is a clear enough statement of intent on its own;
    # requiring a manifest edit as well meant `kcia work IP-116` silently
    # became a prompt whose text happened to be an issue key.
    if not jira.get("enabled", False) and not issue_tracker_connected:
        return "prompt"

    stripped = text.strip()
    project_keys = jira.get("project_keys") or []
    if project_keys:
        pattern = rf"^({'|'.join(re.escape(key) for key in project_keys)})-\d+$"
    else:
        pattern = r"^[A-Z][A-Z0-9]{1,9}-\d+$"

    if re.match(pattern, stripped):
        return "ticket"
    return "prompt"


def new_task_id() -> str:
    return f"t_{uuid.uuid4().hex[:12]}"


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _default_wave_states() -> dict[str, dict[str, Any]]:
    from kcia.waves.definitions import load_waves

    return {
        wave.id: {"status": "pending", "attempts": 0}
        for wave in load_waves()
    }


@dataclass
class Session:
    repo_root: Path
    data: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, repo_root: Path) -> Session:
        path = session_path(repo_root)
        if not path.is_file():
            raise FileNotFoundError(f"No active task session at {path}")
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(repo_root=repo_root.resolve(), data=data)

    @classmethod
    def create(
        cls,
        repo_root: Path,
        *,
        text: str,
        mode: TaskMode,
        title: str | None = None,
        ticket_key: str | None = None,
        active_profiles: list[str] | None = None,
        scope: list[str] | None = None,
    ) -> Session:
        repo_root = repo_root.resolve()
        context_dir(repo_root).mkdir(parents=True, exist_ok=True)
        runs_dir(repo_root).mkdir(parents=True, exist_ok=True)

        cycle = load_cycle(repo_root)
        task: dict[str, Any] = {
            "id": new_task_id(),
            "mode": mode,
            "ticket_key": ticket_key,
            "prompt": text if mode == "prompt" else None,
            "title": title or text,
            "created_at": _now_iso(),
            "branch": cycle.branch if cycle is not None else None,
            "base_branch": cycle.base_branch if cycle is not None else None,
            "scope": scope or [],
        }
        session = cls(
            repo_root=repo_root,
            data={
                "schema_version": SESSION_SCHEMA_VERSION,
                "task": task,
                "active_profiles": active_profiles or [],
                "waves": _default_wave_states(),
                "profile_runs": {},
                "injections": [],
            },
        )
        session.save()
        return session

    def save(self) -> None:
        path = session_path(self.repo_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.data, indent=2), encoding="utf-8")

    @property
    def task(self) -> dict[str, Any]:
        return self.data["task"]

    @property
    def waves(self) -> dict[str, dict[str, Any]]:
        return self.data.setdefault("waves", _default_wave_states())

    def wave_status(self, wave_id: str) -> str:
        # When a wave is parallelizable, the overall status is derived from
        # per-profile wave states.
        if wave_id in {"implementation", "documentation-final"}:
            profile_runs = self.data.get("profile_runs") or {}
            states: list[str] = []
            for profile_run in profile_runs.values():
                waves = profile_run.get("waves") or {}
                state = waves.get(wave_id)
                if isinstance(state, dict) and "status" in state:
                    states.append(str(state["status"]))

            if not states:
                return self.waves.get(wave_id, {}).get("status", "pending")

            if "blocked" in states:
                return "blocked"
            if any(s == "failed" for s in states):
                return "failed"
            if any(s == "running" for s in states):
                return "running"
            if all(s in ("completed", "skipped") for s in states):
                return "completed"
            return "pending"

        return self.waves.get(wave_id, {}).get("status", "pending")

    def set_wave_status(self, wave_id: str, status: WaveStatus, **extra: Any) -> None:
        state = self.waves.setdefault(wave_id, {"status": "pending", "attempts": 0})
        state["status"] = status
        state.update(extra)

    def set_profile_wave_status(
        self, profile_id: str, wave_id: str, status: WaveStatus, **extra: Any
    ) -> None:
        profile_runs: dict[str, Any] = self.data.setdefault("profile_runs", {})
        profile_run = profile_runs.setdefault(profile_id, {"waves": {}})
        waves = profile_run.setdefault("waves", {})
        state = waves.setdefault(wave_id, {"status": "pending", "attempts": 0})
        state["status"] = status
        state.update(extra)
        self.save()

    def acquire_lock(self) -> None:
        self.acquire_lock_for()

    def acquire_lock_for(
        self, wave_id: str | None = None, profile_id: str | None = None
    ) -> None:
        if wave_id is not None and profile_id is not None:
            key = f"{wave_id}:{profile_id}"
            locks = self.data.setdefault("locks", {})
            existing = locks.get(key)
            if existing and _is_lock_alive(existing):
                raise RuntimeError(
                    f"wave lock held for {key} by pid {existing.get('pid')} "
                    f"since {existing.get('acquired_at')}"
                )
            locks[key] = {
                "pid": os.getpid(),
                "tty": os.environ.get("TTY", ""),
                "acquired_at": _now_iso(),
            }
            self.save()
            return

        if self.is_locked():
            holder = self.data.get("lock", {})
            raise RuntimeError(
                f"wave lock held by pid {holder.get('pid')} since {holder.get('acquired_at')}"
            )
        self.data["lock"] = {
            "pid": os.getpid(),
            "tty": os.environ.get("TTY", ""),
            "acquired_at": _now_iso(),
        }
        self.save()

    def release_lock(self) -> None:
        self.release_lock_for()

    def release_lock_for(
        self, wave_id: str | None = None, profile_id: str | None = None
    ) -> None:
        if wave_id is not None and profile_id is not None:
            key = f"{wave_id}:{profile_id}"
            locks = self.data.get("locks") or {}
            locks.pop(key, None)
            if locks:
                self.data["locks"] = locks
            else:
                self.data.pop("locks", None)
            self.save()
            return

        self.data.pop("lock", None)
        self.save()

    def is_locked(self) -> bool:
        lock = self.data.get("lock")
        if lock and _is_lock_alive(lock):
            return True

        locks = self.data.get("locks") or {}
        for record in locks.values():
            if record and _is_lock_alive(record):
                return True

        return False

    def clear_stale_lock(self) -> bool:
        changed = False
        if self.data.get("lock") and not _is_lock_alive(self.data["lock"]):
            self.data.pop("lock", None)
            changed = True

        locks = self.data.get("locks") or {}
        stale_keys: list[str] = []
        for key, record in locks.items():
            if not record or _is_lock_alive(record):
                continue
            stale_keys.append(key)
        for key in stale_keys:
            locks.pop(key, None)
            changed = True

        if changed:
            if locks:
                self.data["locks"] = locks
            else:
                self.data.pop("locks", None)
            self.save()

        return changed

    def is_approved(self, wave_id: str) -> bool:
        return bool(self.data.get("approvals", {}).get(wave_id))

    def approve(self, wave_id: str, *, note: str | None = None) -> None:
        record: dict[str, Any] = {"approved_at": _now_iso()}
        if note:
            record["note"] = note
        self.data.setdefault("approvals", {})[wave_id] = record
        self.save()

    def revoke_approval(self, wave_id: str) -> None:
        self.data.get("approvals", {}).pop(wave_id, None)
        self.save()

    def add_injection(self, text: str) -> None:
        self.data.setdefault("injections", []).append(text)
        self.save()

    def abort(self) -> None:
        close_cycle(self.repo_root)
        path = session_path(self.repo_root)
        if path.is_file():
            path.unlink()


def _is_lock_alive(lock: dict[str, object]) -> bool:
    pid = lock.get("pid")
    if not isinstance(pid, int):
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False

