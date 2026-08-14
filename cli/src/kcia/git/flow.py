"""Git flow: the branching model of a repository, decided once at `kcia start`.

Two models, and no third: either every task opens its own branch off a
development branch, or every task is done on whatever branch you are standing
on. The choice is made once, written to `.ai/local/git.yaml`, and after that the
pipeline just follows it — `kcia wave run` never stops to ask, because a question
in the middle of a run is a question asked at the worst possible moment.

Detection exists to make that one decision cheap, not to be clever: `main` and
`develop` are read off the repository's real branches (local and remote), and
anything ambiguous is asked at init time, where there is a human present.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from kcia.git.repo import current_branch, known_branches

#: Names that mean "the main line", most conventional first.
MAIN_CANDIDATES = ("main", "master")

#: Names that mean "where features are integrated", most conventional first.
DEVELOP_CANDIDATES = ("develop", "development", "dev")

#: Commit type -> git-flow branch prefix.
BRANCH_PREFIXES = {"feat": "feature", "fix": "fix", "docs": "docs"}

GITFLOW = "gitflow"
CURRENT_BRANCH = "current-branch"

MAX_SLUG_WORDS = 6
MAX_SLUG_LENGTH = 48

SCHEMA_VERSION = 1


def git_config_path(repo_root: Path) -> Path:
    return repo_root / ".ai" / "local" / "git.yaml"


@dataclass(frozen=True)
class GitFlow:
    """How this repository branches. `configured` is False until init decides."""

    flow: str = CURRENT_BRANCH
    main_branch: str | None = None
    develop_branch: str | None = None
    base_branch: str | None = None
    configured: bool = False

    @property
    def uses_gitflow(self) -> bool:
        return self.flow == GITFLOW and bool(self.base_branch)

    def describe(self) -> str:
        if self.uses_gitflow:
            return f"git flow — every task branches off `{self.base_branch}`"
        return "no git flow — every task is done on the current branch"


def load_flow(repo_root: Path) -> GitFlow:
    path = git_config_path(repo_root)
    if not path.is_file():
        return GitFlow()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    flow = data.get("flow")
    if flow not in (GITFLOW, CURRENT_BRANCH):
        # Written by an older kcia, which only recorded `base_branch`.
        base = data.get("base_branch")
        return GitFlow(
            flow=GITFLOW if base else CURRENT_BRANCH,
            main_branch=data.get("main_branch"),
            develop_branch=data.get("develop_branch"),
            base_branch=base,
            configured=bool(base),
        )
    return GitFlow(
        flow=flow,
        main_branch=data.get("main_branch"),
        develop_branch=data.get("develop_branch"),
        base_branch=data.get("base_branch"),
        configured=True,
    )


def save_flow(repo_root: Path, flow: GitFlow) -> Path:
    path = git_config_path(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "flow": flow.flow,
        "main_branch": flow.main_branch,
        "develop_branch": flow.develop_branch,
        "base_branch": flow.base_branch,
    }
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return path


@dataclass(frozen=True)
class BranchGuess:
    """What the repository suggests for one role, and whether it is certain."""

    name: str | None
    certain: bool
    candidates: tuple[str, ...] = ()


def _guess(branches: list[str], candidates: tuple[str, ...]) -> BranchGuess:
    found = [name for name in candidates if name in branches]
    if len(found) == 1:
        return BranchGuess(found[0], True, tuple(found))
    if not found:
        return BranchGuess(None, False, ())
    # Several conventions coexist (`main` *and* `master`): a human picks.
    return BranchGuess(found[0], False, tuple(found))


def detect_branches(repo_root: Path) -> tuple[BranchGuess, BranchGuess]:
    """(main, develop) as read from the repository's real branches."""
    branches = known_branches(repo_root)
    return _guess(branches, MAIN_CANDIDATES), _guess(branches, DEVELOP_CANDIDATES)


@dataclass(frozen=True)
class BaseBranch:
    """The branch a new task starts from, and why."""

    name: str
    certain: bool
    reason: str
    candidates: tuple[str, ...] = ()


def detect_base_branch(repo_root: Path) -> BaseBranch:
    """Where `kcia branch start` would branch from.

    The configured flow decides. Without one (a repository initialized before
    git flow was configurable) it falls back to reading the branches, so the
    manual `kcia branch start` keeps working on its own.
    """
    configured = load_flow(repo_root)
    branches = known_branches(repo_root)
    current = current_branch(repo_root)

    if configured.configured:
        if not configured.uses_gitflow:
            return BaseBranch(current, True, "git flow is off for this repository")
        base = configured.base_branch or ""
        if base in branches:
            return BaseBranch(base, True, "configured in .ai/local/git.yaml")
        return BaseBranch(
            current,
            False,
            f"the configured base branch `{base}` no longer exists",
            tuple(dict.fromkeys([*branches, current])),
        )

    conventional = [
        name for name in (*DEVELOP_CANDIDATES, *MAIN_CANDIDATES) if name in branches
    ]
    candidates = tuple(dict.fromkeys([*conventional, current]))
    if current in conventional:
        return BaseBranch(current, True, "the current branch is a base branch", candidates)
    if len(conventional) == 1:
        return BaseBranch(
            conventional[0],
            True,
            f"`{conventional[0]}` is the only base branch in this repository",
            candidates,
        )
    return BaseBranch(
        current,
        False,
        "the repository has more than one candidate and none is configured"
        if conventional
        else "no `develop`, `main` or `master` branch was found",
        candidates,
    )


def save_base_branch(repo_root: Path, base: str) -> None:
    """Record a base branch chosen outside init (`kcia branch start --base`)."""
    current = load_flow(repo_root)
    save_flow(
        repo_root,
        GitFlow(
            flow=GITFLOW,
            main_branch=current.main_branch,
            develop_branch=current.develop_branch or base,
            base_branch=base,
            configured=True,
        ),
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
