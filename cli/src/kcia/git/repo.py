"""Thin wrapper over the `git` binary.

kcia never links a git library and never holds a credential: every operation is
the same `git` the user already runs in that repository, so the remote, the
signing key and the auth helper are whatever their machine is configured with.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

GIT_BIN = "git"
GH_BIN = "gh"


class GitError(RuntimeError):
    """A git invocation failed; the message carries git's own stderr."""


@dataclass(frozen=True)
class Change:
    """One entry of `git status --porcelain`."""

    path: str
    index: str
    worktree: str

    @property
    def staged(self) -> bool:
        return self.index not in (" ", "?")

    @property
    def untracked(self) -> bool:
        return self.index == "?"


def git_available() -> bool:
    return shutil.which(GIT_BIN) is not None


def gh_available() -> bool:
    return shutil.which(GH_BIN) is not None


def run_git(repo_root: Path, *args: str) -> str:
    if not git_available():
        raise GitError("`git` is not installed. Install it and re-run.")
    result = subprocess.run(
        [GIT_BIN, *args],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise GitError(f"git {' '.join(args)} failed: {detail}")
    return result.stdout


def is_git_repo(repo_root: Path) -> bool:
    try:
        return run_git(repo_root, "rev-parse", "--is-inside-work-tree").strip() == "true"
    except GitError:
        return False


def current_branch(repo_root: Path) -> str:
    """The checked-out branch, or `HEAD` when detached."""
    return run_git(repo_root, "rev-parse", "--abbrev-ref", "HEAD").strip()


def has_commits(repo_root: Path) -> bool:
    try:
        run_git(repo_root, "rev-parse", "--verify", "HEAD")
    except GitError:
        return False
    return True


def local_branches(repo_root: Path) -> list[str]:
    out = run_git(repo_root, "for-each-ref", "--format=%(refname:short)", "refs/heads")
    return [line.strip() for line in out.splitlines() if line.strip()]


def remote_branches(repo_root: Path) -> list[str]:
    """Remote branches with the remote name stripped (`origin/develop` -> `develop`)."""
    out = run_git(repo_root, "for-each-ref", "--format=%(refname:short)", "refs/remotes")
    names = []
    for line in out.splitlines():
        name = line.strip()
        if not name or name.endswith("/HEAD"):
            continue
        _, _, short = name.partition("/")
        if short:
            names.append(short)
    return names


def known_branches(repo_root: Path) -> list[str]:
    """Local branches first, then remote-only ones, without duplicates."""
    seen = list(local_branches(repo_root))
    for name in remote_branches(repo_root):
        if name not in seen:
            seen.append(name)
    return seen


def branch_exists(repo_root: Path, name: str) -> bool:
    return name in known_branches(repo_root)


def remotes(repo_root: Path) -> list[str]:
    return [line.strip() for line in run_git(repo_root, "remote").splitlines() if line.strip()]


def remote_url(repo_root: Path, remote: str = "origin") -> str | None:
    try:
        return run_git(repo_root, "remote", "get-url", remote).strip() or None
    except GitError:
        return None


def changes(repo_root: Path) -> list[Change]:
    out = run_git(repo_root, "status", "--porcelain=v1", "--untracked-files=all")
    entries: list[Change] = []
    for line in out.splitlines():
        if len(line) < 4:
            continue
        index, worktree, path = line[0], line[1], line[3:]
        # Renames are reported as `old -> new`; the new path is what we commit.
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        entries.append(Change(path=path.strip('"'), index=index, worktree=worktree))
    return entries


def is_dirty(repo_root: Path) -> bool:
    return bool(changes(repo_root))


def create_branch(repo_root: Path, name: str, *, base: str) -> None:
    run_git(repo_root, "checkout", "-b", name, base)


def checkout(repo_root: Path, name: str) -> None:
    run_git(repo_root, "checkout", name)


def stage(repo_root: Path, paths: list[str]) -> None:
    if not paths:
        return
    run_git(repo_root, "add", "--", *paths)


def unstage_all(repo_root: Path) -> None:
    """Reset the index without touching the worktree, so kcia stages explicitly.

    A commit that silently swept in whatever the user had staged for an unrelated
    reason is the failure this avoids.
    """
    if has_commits(repo_root):
        run_git(repo_root, "reset", "--quiet", "HEAD", "--")
    else:
        run_git(repo_root, "rm", "--cached", "-r", "--quiet", "--ignore-unmatch", ".")


def commit(repo_root: Path, message: str) -> str:
    run_git(repo_root, "commit", "-m", message)
    return run_git(repo_root, "rev-parse", "--short", "HEAD").strip()


def push(repo_root: Path, branch: str, *, remote: str = "origin") -> None:
    run_git(repo_root, "push", "--set-upstream", remote, branch)
