"""Work cycle: one branch until `kcia done`."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from kcia.git.autobranch import ensure_task_branch
from kcia.git.cycle import close_cycle, is_cycle_open, load_cycle, open_cycle
from kcia.git.flow import GITFLOW, GitFlow, save_flow
from kcia.git.repo import current_branch
from kcia.waves.session import Session, session_path


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


def gitflow(repo: Path) -> None:
    save_flow(
        repo,
        GitFlow(
            flow=GITFLOW,
            main_branch="main",
            develop_branch="develop",
            base_branch="develop",
            configured=True,
        ),
    )


def test_cycle_round_trip(repo: Path) -> None:
    open_cycle(repo, "feature/foo", "develop", task_id="t_abc")
    cycle = load_cycle(repo)
    assert cycle is not None
    assert cycle.branch == "feature/foo"
    assert cycle.base_branch == "develop"
    assert cycle.task_id == "t_abc"
    assert is_cycle_open(repo)
    assert close_cycle(repo)
    assert load_cycle(repo) is None


def test_open_cycle_reuses_branch_for_new_task(repo: Path) -> None:
    gitflow(repo)
    first = Session.create(repo, text="fix overflow", mode="prompt", title="fix overflow")
    outcome = ensure_task_branch(first)
    assert outcome is not None and outcome.created
    branch = current_branch(repo)
    assert is_cycle_open(repo)

    second = Session.create(repo, text="still broken", mode="prompt", title="still broken")
    assert second.task["branch"] == branch
    outcome = ensure_task_branch(second)
    assert outcome is None
    assert current_branch(repo) == branch


def test_done_closes_cycle_and_session(repo: Path, monkeypatch) -> None:
    import subprocess

    from git_helpers import add_origin
    from typer.testing import CliRunner

    from kcia.main import app

    add_origin(repo)
    git(repo, "push", "-u", "origin", "develop")
    gitflow(repo)
    session = Session.create(repo, text="add loader", mode="prompt", title="add loader")
    ensure_task_branch(session)
    (repo / "src.txt").write_text("x\n", encoding="utf-8")

    monkeypatch.chdir(repo)
    monkeypatch.setattr("kcia.commands.commit.gh_available", lambda: True)
    real_run = subprocess.run

    def fake_run(args, **kwargs):
        if args and args[0] == "gh":
            return subprocess.CompletedProcess(
                args, 0, stdout="https://example.com/pull/1\n", stderr=""
            )
        return real_run(args, **kwargs)

    monkeypatch.setattr("kcia.commands.commit.subprocess.run", fake_run)

    runner = CliRunner()
    result = runner.invoke(app, ["done", "--yes"], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    assert not is_cycle_open(repo)
    assert not session_path(repo).is_file()


def test_abort_closes_cycle(repo: Path) -> None:
    gitflow(repo)
    session = Session.create(repo, text="add loader", mode="prompt", title="add loader")
    ensure_task_branch(session)
    assert is_cycle_open(repo)
    session.abort()
    assert not is_cycle_open(repo)
    assert not session_path(repo).is_file()
