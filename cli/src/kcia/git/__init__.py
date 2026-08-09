"""Git operations: git-flow branching and the commits that close a task."""

from kcia.git.commit import (
    COMMIT_TYPES,
    NothingToCommit,
    PlannedCommit,
    build_message,
    plan_commits,
    split_changes,
)
from kcia.git.flow import BaseBranch, branch_name, detect_base_branch, save_base_branch
from kcia.git.repo import GitError, gh_available, git_available

__all__ = [
    "COMMIT_TYPES",
    "BaseBranch",
    "GitError",
    "NothingToCommit",
    "PlannedCommit",
    "branch_name",
    "build_message",
    "detect_base_branch",
    "gh_available",
    "git_available",
    "plan_commits",
    "save_base_branch",
    "split_changes",
]
