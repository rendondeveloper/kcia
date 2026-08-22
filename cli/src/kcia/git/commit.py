"""Build the commits that close a task: what goes in them and how they read."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from kcia.git.repo import Change, changes

#: The only accepted commit types.
COMMIT_TYPES = ("feat", "fix", "docs")

#: Everything the pipeline writes lives here; these subtrees are regenerable
#: output and are gitignored, so they never reach a commit.
AI_DIR = ".ai/"
EXCLUDED_AI_SUBTREES = (".ai/local/", ".ai/cache/", ".ai/generated/")

_FIX_HINTS = ("fix", "bug", "arregla", "corrige", "error", "crash", "hotfix")
_DOCS_HINTS = ("doc", "documenta", "readme", "guía", "guia")


class NothingToCommit(RuntimeError):
    """The worktree is clean, so there is no commit to make."""


@dataclass(frozen=True)
class PlannedCommit:
    """One commit, ready to be shown to the user before anything is written."""

    kind: str  # "plan" or "code"
    commit_type: str
    subject: str
    ticket: str | None
    paths: list[str] = field(default_factory=list)

    @property
    def message(self) -> str:
        return build_message(self.commit_type, ticket=self.ticket, subject=self.subject)


def build_message(commit_type: str, *, ticket: str | None, subject: str) -> str:
    """`feat: IP-116 - subject`, or `feat: subject` when the task has no ticket.

    Without a ticket nothing is substituted — no placeholder key is invented,
    because a key that does not exist is worse than no key at all.
    """
    if commit_type not in COMMIT_TYPES:
        raise ValueError(f"unknown commit type '{commit_type}'; use one of {', '.join(COMMIT_TYPES)}")
    subject = " ".join(subject.split()).rstrip(".")
    if not subject:
        raise ValueError("a commit needs a subject")
    key = (ticket or "").strip().upper()
    if key:
        return f"{commit_type}: {key} - {subject}"
    return f"{commit_type}: {subject}"


def is_plan_path(path: str) -> bool:
    """Whether this path is pipeline documentation rather than product code."""
    normalized = path.replace("\\", "/")
    if not normalized.startswith(AI_DIR):
        return False
    return not any(normalized.startswith(subtree) for subtree in EXCLUDED_AI_SUBTREES)


def is_committable(path: str) -> bool:
    normalized = path.replace("\\", "/")
    if any(normalized.startswith(subtree) for subtree in EXCLUDED_AI_SUBTREES):
        return False
    return True


def split_changes(entries: list[Change]) -> tuple[list[str], list[str]]:
    """Split the worktree into (plan paths, code paths)."""
    plan: list[str] = []
    code: list[str] = []
    for entry in entries:
        if not is_committable(entry.path):
            continue
        (plan if is_plan_path(entry.path) else code).append(entry.path)
    return sorted(plan), sorted(code)


def infer_commit_type(subject: str, code_paths: list[str]) -> str:
    """Best guess at the type; `kcia commit --type` always wins over it."""
    lowered = subject.lower()
    if not code_paths:
        return "docs"
    if any(hint in lowered for hint in _FIX_HINTS):
        return "fix"
    if any(hint in lowered for hint in _DOCS_HINTS) and all(
        path.endswith((".md", ".mdc", ".txt", ".rst")) for path in code_paths
    ):
        return "docs"
    return "feat"


def plan_commits(
    repo_root: Path,
    *,
    subject: str,
    ticket: str | None,
    commit_type: str | None = None,
    plan_type: str | None = None,
    single: bool = False,
) -> list[PlannedCommit]:
    """Compose the commits for the current worktree, newest work last.

    The plan is committed separately and first: it is the record of *why* the
    code changed, and keeping it in its own `docs:` commit means reviewers can
    read the intent without it being buried in the diff.

    Type precedence: explicit `commit_type` (`kcia done --type`) always wins,
    then `plan_type` (from the canonical plan's `type` field), then the
    keyword-based `infer_commit_type` guess as a last-resort fallback for
    tasks with no plan.
    """
    plan_paths, code_paths = split_changes(changes(repo_root))
    if not plan_paths and not code_paths:
        raise NothingToCommit("nothing to commit — the worktree is clean.")

    resolved_type = commit_type or plan_type or infer_commit_type(subject, code_paths)
    if resolved_type not in COMMIT_TYPES:
        raise ValueError(
            f"unknown commit type '{resolved_type}'; use one of {', '.join(COMMIT_TYPES)}"
        )

    if single:
        return [
            PlannedCommit(
                kind="code",
                commit_type=resolved_type,
                subject=subject,
                ticket=ticket,
                paths=plan_paths + code_paths,
            )
        ]

    commits: list[PlannedCommit] = []
    if plan_paths:
        commits.append(
            PlannedCommit(
                kind="plan",
                commit_type="docs",
                subject=f"plan — {subject}",
                ticket=ticket,
                paths=plan_paths,
            )
        )
    if code_paths:
        commits.append(
            PlannedCommit(
                kind="code",
                commit_type=resolved_type,
                subject=subject,
                ticket=ticket,
                paths=code_paths,
            )
        )
    return commits
