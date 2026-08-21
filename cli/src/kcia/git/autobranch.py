"""Open the task's branch automatically, following the configured flow.

`kcia work` never asks: the branching model was decided at `kcia init` and
lives in `.ai/local/git.yaml`. This module turns that decision into an action
once per work cycle (until `kcia done`), and gets out of the way — a failure
here reports itself and lets the run continue, because not being on the ideal
branch is never a reason to refuse to do the work.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from kcia.git.commit import infer_commit_type
from kcia.git.cycle import load_cycle, open_cycle, WorkCycle
from kcia.git.flow import branch_name, load_flow
from kcia.git.repo import (
    GitError,
    branch_exists,
    checkout,
    create_branch,
    current_branch,
    is_git_repo,
)
from kcia.waves.session import Session


@dataclass(frozen=True)
class BranchOutcome:
    """What happened, as one line for the user."""

    message: str
    branch: str | None = None
    created: bool = False


def _adopt_open_cycle(
    session: Session,
    repo: Path,
    cycle: WorkCycle,
    here: str,
) -> BranchOutcome | None:
    """Keep an open work cycle on its branch; never open a second one."""
    task = session.data.get("task")
    if not isinstance(task, dict):
        return None

    task["branch"] = cycle.branch
    if cycle.base_branch:
        task["base_branch"] = cycle.base_branch
    session.save()

    if cycle.branch == here:
        return None

    if not branch_exists(repo, cycle.branch):
        return BranchOutcome(
            f"warning: open work cycle recorded `{cycle.branch}` but that branch "
            f"no longer exists. You are on `{here}`. Close with `kcia done` or "
            "`kcia work abort`, or fix git manually.",
            branch=cycle.branch,
        )

    try:
        checkout(repo, cycle.branch)
    except GitError as exc:
        return BranchOutcome(
            f"warning: open work cycle is on `{cycle.branch}` but checkout failed "
            f"({exc}). You are on `{here}`.",
            branch=cycle.branch,
        )
    return BranchOutcome(
        f"Git flow: open work cycle on `{cycle.branch}` — checked out from `{here}`.",
        branch=cycle.branch,
    )


def ensure_task_branch(session: Session) -> BranchOutcome | None:
    """Put the task on its own branch if the repository is configured for it.

    Returns None when there is nothing to say (no git flow, or the task is
    already on its branch).
    """
    repo = session.repo_root
    if not is_git_repo(repo):
        return None

    flow = load_flow(repo)
    if not flow.uses_gitflow:
        return None

    task = session.data.get("task")
    if not isinstance(task, dict):
        return None

    try:
        here = current_branch(repo)
    except GitError as exc:
        return BranchOutcome(f"warning: could not read the current branch ({exc}).")

    cycle = load_cycle(repo)
    if cycle is not None:
        return _adopt_open_cycle(session, repo, cycle, here)

    recorded = task.get("branch")
    if recorded:
        if recorded == here:
            base = task.get("base_branch")
            if isinstance(base, str) and base:
                open_cycle(repo, recorded, base, task_id=task.get("id"))
            return None
        return BranchOutcome(
            f"warning: this task's branch is `{recorded}` but you are on `{here}`. "
            "Nothing was changed.",
            branch=recorded,
        )

    base = flow.base_branch or ""
    if not branch_exists(repo, base):
        return BranchOutcome(
            f"warning: the configured base branch `{base}` does not exist, so the task "
            f"stays on `{here}`. Fix it in .ai/local/git.yaml."
        )

    subject = (task.get("title") or task.get("prompt") or "").strip()
    ticket = task.get("ticket_key")
    if not subject and not ticket:
        return None

    target = branch_name(
        infer_commit_type(subject, ["src"]), ticket=ticket, subject=subject or (ticket or "task")
    )
    if branch_exists(repo, target):
        # Someone already opened it (a retried task, a colleague's branch name).
        # Adopting it silently could put the work somewhere unexpected.
        return BranchOutcome(
            f"warning: branch `{target}` already exists, so the task stays on `{here}`. "
            f"Check it out yourself, or use `kcia branch start --name <other>`.",
        )

    try:
        create_branch(repo, target, base=base)
    except GitError as exc:
        return BranchOutcome(f"warning: could not branch off `{base}` ({exc}); staying on `{here}`.")

    task["branch"] = target
    task["base_branch"] = base
    session.save()
    open_cycle(repo, target, base, task_id=task.get("id"))
    return BranchOutcome(f"Git flow: created `{target}` from `{base}`.", branch=target, created=True)
