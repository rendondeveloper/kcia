"""Git-flow: which branch we start from, and what the new branch is called.

The base branch is the one decision kcia cannot infer safely. A repository that
uses `develop` and one that merges straight into `main` look identical from the
worktree, so the rule here is deliberately conservative: propose the current
branch, and treat anything that is not an unambiguous convention as a question
for the user rather than a guess. The answer is remembered per repository.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from kcia.git.repo import current_branch, known_branches

#: Branch names that need no confirmation, most specific convention first.
CONVENTIONAL_BASES = ("develop", "main", "master")

#: Commit type -> git-flow branch prefix.
BRANCH_PREFIXES = {"feat": "feature", "fix": "fix", "docs": "docs"}

MAX_SLUG_WORDS = 6
MAX_SLUG_LENGTH = 48


def git_config_path(repo_root: Path) -> Path:
    return repo_root / ".ai" / "local" / "git.yaml"


def load_git_config(repo_root: Path) -> dict:
    path = git_config_path(repo_root)
    if not path.is_file():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def save_base_branch(repo_root: Path, base: str) -> None:
    """Remember the answer so the question is asked once per repository."""
    path = git_config_path(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    config = load_git_config(repo_root)
    config["schema_version"] = 1
    config["base_branch"] = base
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")


@dataclass(frozen=True)
class BaseBranch:
    """A proposed starting point and whether it needs confirming."""

    name: str
    certain: bool
    reason: str
    candidates: tuple[str, ...] = ()


def detect_base_branch(repo_root: Path) -> BaseBranch:
    """Propose the branch to start from.

    Order: a remembered answer, then the current branch when it is a known
    convention, then `develop`/`main`/`master` if exactly one of them exists.
    Anything else is uncertain and the caller must ask.
    """
    remembered = load_git_config(repo_root).get("base_branch")
    branches = known_branches(repo_root)
    if remembered and remembered in branches:
        return BaseBranch(remembered, True, "remembered in .ai/local/git.yaml")

    current = current_branch(repo_root)
    conventional = [name for name in CONVENTIONAL_BASES if name in branches]
    candidates = tuple(dict.fromkeys([*conventional, current]))

    if current in CONVENTIONAL_BASES:
        return BaseBranch(current, True, "the current branch is a base branch", candidates)
    if len(conventional) == 1:
        return BaseBranch(
            conventional[0],
            True,
            f"`{conventional[0]}` is the only base branch in this repository",
            candidates,
        )

    # Either several conventions coexist (develop *and* main) or none does, and
    # the current branch is some feature branch. Both are the user's call.
    return BaseBranch(
        current,
        False,
        "the current branch is not a base branch and the repository has more than one candidate"
        if conventional
        else "no `develop`, `main` or `master` branch was found",
        candidates,
    )


def slugify(text: str) -> str:
    words = re.findall(r"[a-z0-9]+", text.lower())
    slug = "-".join(words[:MAX_SLUG_WORDS])
    return slug[:MAX_SLUG_LENGTH].strip("-")


def branch_name(commit_type: str, *, ticket: str | None, subject: str) -> str:
    """`feature/IP-116-add-commit-flow`, or without the key when there is no ticket."""
    prefix = BRANCH_PREFIXES.get(commit_type, "feature")
    slug = slugify(subject) or "task"
    if ticket:
        return f"{prefix}/{ticket.strip().upper()}-{slug}"
    return f"{prefix}/{slug}"
