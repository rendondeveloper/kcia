"""Path resolution helpers."""

from __future__ import annotations

from pathlib import Path


def find_repo_root(start: Path | None = None) -> Path | None:
    current = (start or Path.cwd()).resolve()
    for candidate in [current, *current.parents]:
        if (candidate / ".ai").is_dir() or (candidate / ".git").is_dir():
            return candidate
    return None


def control_plane_root() -> Path:
    return Path(__file__).resolve().parents[3] / "control-plane"
