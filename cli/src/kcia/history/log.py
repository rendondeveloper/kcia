"""Append-only, git-tracked log of work sessions: `.ai/history/sessions.jsonl`.

One JSON object per line. Never rewritten in place — only ever appended to —
so it stays diffable and mergeable in git, the same way a changelog would.
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from kcia.git.repo import GitError, changes, current_branch

LOG_PATH = Path(".ai") / "history" / "sessions.jsonl"

CHANGE_KINDS = ("created", "modified", "deleted")


@dataclass(frozen=True)
class SessionEntry:
    id: str
    timestamp: str
    title: str
    summary: str
    decisions: list[str] = field(default_factory=list)
    files: list[dict[str, str]] = field(default_factory=list)
    commit_sha: str | None = None
    branch: str | None = None
    task_id: str | None = None

    def to_json(self) -> dict:
        return {
            "id": self.id,
            "timestamp": self.timestamp,
            "title": self.title,
            "summary": self.summary,
            "decisions": self.decisions,
            "files": self.files,
            "commit_sha": self.commit_sha,
            "branch": self.branch,
            "task_id": self.task_id,
        }

    @classmethod
    def from_json(cls, data: dict) -> "SessionEntry":
        return cls(
            id=data["id"],
            timestamp=data["timestamp"],
            title=data["title"],
            summary=data.get("summary", ""),
            decisions=list(data.get("decisions") or []),
            files=list(data.get("files") or []),
            commit_sha=data.get("commit_sha"),
            branch=data.get("branch"),
            task_id=data.get("task_id"),
        )


def new_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{secrets.token_hex(3)}"


def log_path(repo_root: Path) -> Path:
    return repo_root / LOG_PATH


def entry_from_git(
    repo_root: Path,
    *,
    title: str,
    summary: str,
    decisions: list[str],
    files: list[dict[str, str]] | None,
    commit_sha: str | None,
    task_id: str | None,
) -> SessionEntry:
    """Build an entry, deriving `files`/`branch` from git when not given explicitly."""
    try:
        branch = current_branch(repo_root)
    except GitError:
        branch = None

    resolved_files = files if files is not None else _files_from_worktree(repo_root)

    return SessionEntry(
        id=new_id(),
        timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        title=title,
        summary=summary,
        decisions=decisions,
        files=resolved_files,
        commit_sha=commit_sha,
        branch=branch,
        task_id=task_id,
    )


def _files_from_worktree(repo_root: Path) -> list[dict[str, str]]:
    """Best-effort file list from the working tree when the caller didn't pass one."""
    try:
        entries = changes(repo_root)
    except GitError:
        return []
    result: list[dict[str, str]] = []
    for change in entries:
        if change.index == "D" or change.worktree == "D":
            kind = "deleted"
        elif change.untracked:
            kind = "created"
        else:
            kind = "modified"
        result.append({"path": change.path, "change": kind})
    return result


def append_entry(repo_root: Path, entry: SessionEntry) -> Path:
    path = log_path(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(entry.to_json(), ensure_ascii=False)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")
    return path


def read_entries(repo_root: Path) -> list[SessionEntry]:
    path = log_path(repo_root)
    if not path.is_file():
        return []
    entries: list[SessionEntry] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        entries.append(SessionEntry.from_json(json.loads(line)))
    return entries
