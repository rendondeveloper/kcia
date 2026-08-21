"""Shared git helpers for tests."""

from __future__ import annotations

import subprocess
from pathlib import Path


def add_origin(repo: Path) -> Path:
    """Attach a local bare `origin` and push the current HEAD to it."""
    origin = repo.parent / f"{repo.name}-origin.git"
    subprocess.run(
        ["git", "init", "--bare", "--initial-branch=main", str(origin)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "remote", "add", "origin", str(origin)],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "push", "-u", "origin", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    return origin
