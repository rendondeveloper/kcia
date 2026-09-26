"""`kcia done` runs the post-commit workflow configured at init."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from git_helpers import add_origin
from kcia.git.autobranch import ensure_task_branch
from kcia.git.cycle import is_cycle_open
from kcia.git.flow import CURRENT_BRANCH, GITFLOW, GitFlow, save_flow
from kcia.git.repo import current_branch
from kcia.main import app
from kcia.waves.session import Session, session_path

runner = CliRunner()


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "--initial-branch=main")
    git(root, "config", "user.email", "test@example.com")
    git(root, "config", "user.name", "Test")
    (root / "README.md").write_text("hi\n", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-m", "initial")
    git(root, "branch", "develop")
    return root


def _gitflow(repo: Path, *, on_done: str = "pr") -> None:
    save_flow(
        repo,
        GitFlow(
            flow=GITFLOW,
            main_branch="main",
            develop_branch="develop",
            base_branch="develop",
            on_done=on_done,
            configured=True,
        ),
    )


def _current_branch_flow(repo: Path) -> None:
    save_flow(
        repo,
        GitFlow(flow=CURRENT_BRANCH, main_branch="main", configured=True),
    )


def _fake_gh(monkeypatch, *, pr_edit_calls: list | None = None) -> None:
    monkeypatch.setattr("kcia.commands.commit.gh_available", lambda: True)
    real_run = subprocess.run

    def fake_run(args, **kwargs):
        if args and args[0] == "gh":
            if len(args) >= 3 and args[1] == "pr" and args[2] == "edit":
                if pr_edit_calls is not None:
                    pr_edit_calls.append(list(args))
                return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
            return subprocess.CompletedProcess(
                args, 0, stdout="https://example.com/pull/1\n", stderr=""
            )
        return real_run(args, **kwargs)

    monkeypatch.setattr("kcia.commands.commit.subprocess.run", fake_run)


def test_current_branch_done_pushes(repo: Path, monkeypatch) -> None:
    add_origin(repo)
    _current_branch_flow(repo)
    (repo / "src.txt").write_text("x\n", encoding="utf-8")
    Session.create(repo, text="add loader", mode="prompt", title="add loader")
    monkeypatch.chdir(repo)
    result = runner.invoke(app, ["done", "--yes"], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    assert "Pushing `main`" in result.output
    assert "Pushed `main`" in result.output


def test_current_branch_done_fails_without_a_remote(repo: Path, monkeypatch) -> None:
    _current_branch_flow(repo)
    (repo / "src.txt").write_text("x\n", encoding="utf-8")
    Session.create(repo, text="add loader", mode="prompt", title="add loader")
    monkeypatch.chdir(repo)
    result = runner.invoke(app, ["done", "--yes"], catch_exceptions=False)
    assert result.exit_code == 1
    assert "No git remote configured" in result.output


def test_gitflow_pr_with_reviewers_requests_github_review(repo: Path, monkeypatch) -> None:
    add_origin(repo)
    git(repo, "push", "-u", "origin", "develop")
    save_flow(
        repo,
        GitFlow(
            flow=GITFLOW,
            main_branch="main",
            develop_branch="develop",
            base_branch="develop",
            on_done="pr",
            reviewers=("octocat", "acme/team"),
            configured=True,
        ),
    )
    session = Session.create(repo, text="add loader", mode="prompt", title="add loader")
    ensure_task_branch(session)
    (repo / "src.txt").write_text("x\n", encoding="utf-8")
    reviewer_calls: list = []
    monkeypatch.setattr(
        "kcia.integrations.github_reviews.request_reviewers",
        lambda repo, url, reviewers: reviewer_calls.append((url, tuple(reviewers))),
    )
    _fake_gh(monkeypatch)
    monkeypatch.chdir(repo)
    result = runner.invoke(app, ["done", "--yes"], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    assert reviewer_calls == [
        ("https://example.com/pull/1", ("octocat", "acme/team")),
    ]
    assert "Requested PR review from" in result.output


def test_gitflow_pr_pushes_and_opens_a_pr(repo: Path, monkeypatch) -> None:
    add_origin(repo)
    git(repo, "push", "-u", "origin", "develop")
    _gitflow(repo, on_done="pr")
    session = Session.create(repo, text="add loader", mode="prompt", title="add loader")
    ensure_task_branch(session)
    (repo / "src.txt").write_text("x\n", encoding="utf-8")
    _fake_gh(monkeypatch)
    monkeypatch.chdir(repo)
    result = runner.invoke(app, ["done", "--yes"], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    assert "Opening PR to `develop`" in result.output
    assert "https://example.com/pull/1" in result.output
    assert not is_cycle_open(repo)
    assert not session_path(repo).is_file()


def test_gitflow_pr_fails_without_gh(repo: Path, monkeypatch) -> None:
    add_origin(repo)
    _gitflow(repo, on_done="pr")
    session = Session.create(repo, text="add loader", mode="prompt", title="add loader")
    ensure_task_branch(session)
    (repo / "src.txt").write_text("x\n", encoding="utf-8")
    monkeypatch.setattr("kcia.commands.commit.gh_available", lambda: False)
    monkeypatch.chdir(repo)
    result = runner.invoke(app, ["done", "--yes"], catch_exceptions=False)
    assert result.exit_code == 1
    assert "`gh` is not installed" in result.output


def test_gitflow_merge_merges_into_base_and_deletes_the_task_branch(
    repo: Path, monkeypatch
) -> None:
    add_origin(repo)
    git(repo, "push", "-u", "origin", "develop")
    _gitflow(repo, on_done="merge")
    session = Session.create(repo, text="add loader", mode="prompt", title="add loader")
    outcome = ensure_task_branch(session)
    assert outcome is not None and outcome.created
    task_branch = current_branch(repo)
    (repo / "src.txt").write_text("x\n", encoding="utf-8")
    monkeypatch.chdir(repo)
    result = runner.invoke(app, ["done", "--yes"], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    assert f"Merging `{task_branch}` into `develop`" in result.output
    assert f"Merged `{task_branch}` into `develop`" in result.output
    assert current_branch(repo) == "develop"
    listed = subprocess.run(
        ["git", "branch", "--list", task_branch],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert listed == ""
    assert not is_cycle_open(repo)
    assert not session_path(repo).is_file()


def test_jira_done_selects_next_subtask_and_runs_existing_workflow(repo, monkeypatch) -> None:
    from kcia.commands import jira as jira_commands
    from kcia.integrations import jira_cycle as jira

    add_origin(repo)
    _current_branch_flow(repo)
    def issue(key):
        return jira.Issue(key=key, summary=f"Implement {key}", description=key,
                          status="Open", category="new", priority="High", priority_rank=0)
    snapshot = jira.Snapshot(parent=issue("PR-1"), subtasks=[issue("PR-2"), issue("PR-3")], complete=True)
    monkeypatch.setattr(jira, "fetch", lambda *a: snapshot)
    transitions = []
    monkeypatch.setattr(jira, "transition", lambda *args: transitions.append(args[1:]))
    session = jira_commands.select(repo, key="PR-1", subtask="PR-2")
    for wave in session.waves.values():
        wave["status"] = "completed"
    session.save()
    (repo / "src.txt").write_text("implemented\n")
    monkeypatch.chdir(repo)
    from types import SimpleNamespace
    monkeypatch.setattr(jira_commands, "sys", SimpleNamespace(stdin=SimpleNamespace(isatty=lambda: True)))
    monkeypatch.setattr(jira_commands.typer, "prompt", lambda *a, **kw: kw["default"])
    executions = []
    monkeypatch.setattr("kcia.commands.work._execute", lambda session, **kw: executions.append(session))
    result = runner.invoke(app, ["done", "--yes"], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    assert executions[0].task["ticket_key"] == "PR-3"
    assert ("PR-2", "finish") in transitions
    assert ("PR-1", "parent_finish") not in transitions
    assert jira.load(repo)["delivered"] == ["PR-2"]


def test_autocycle_done_opens_pr_and_waits_for_approval(repo: Path, monkeypatch) -> None:
    from kcia.integrations import jira_cycle as jira

    add_origin(repo)
    git(repo, "push", "-u", "origin", "develop")
    _gitflow(repo, on_done="pr")
    session = Session.create(repo, text="PR-2", mode="ticket", ticket_key="PR-2", title="Implement PR-2")
    session.data["jira_cycle"] = True
    session.data["jira_autocycle"] = True
    for wave in session.waves.values():
        wave["status"] = "completed"
    session.save()
    ensure_task_branch(session)
    (repo / "src.txt").write_text("x\n", encoding="utf-8")
    transitions = []
    monkeypatch.setattr(jira, "transition", lambda *args: transitions.append(args[1:]))
    _fake_gh(monkeypatch)
    monkeypatch.chdir(repo)

    result = runner.invoke(app, ["done", "--yes"], catch_exceptions=False)

    assert result.exit_code == 0, result.output
    reloaded = Session.load(repo)
    checkpoint = reloaded.data["done_checkpoint"]
    assert checkpoint["awaiting_pr_approval"] is True
    assert checkpoint["approval_transitioned"] is True
    assert checkpoint["pull_request_url"] == "https://example.com/pull/1"
    assert ("PR-2", "approval") in transitions
    assert is_cycle_open(repo)
    assert session_path(repo).is_file()
