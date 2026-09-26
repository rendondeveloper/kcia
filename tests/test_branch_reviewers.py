"""`kcia branch reviewer` commands and PR reviewer configuration."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from kcia.git.flow import (
    GITFLOW,
    GitFlow,
    GitFlowConfigError,
    ON_DONE_MERGE,
    git_config_path,
    load_flow,
    normalize_reviewer_identifier,
    normalize_reviewers_list,
    save_flow,
    save_base_branch,
)
from kcia.main import app

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


def test_normalize_reviewer_strips_at_and_whitespace() -> None:
    assert normalize_reviewer_identifier("  @alice  ") == "alice"
    assert normalize_reviewers_list(["Alice", "alice"]) == ("Alice",)


def test_normalize_reviewer_rejects_invalid() -> None:
    with pytest.raises(ValueError):
        normalize_reviewer_identifier("")
    with pytest.raises(ValueError):
        normalize_reviewer_identifier("acme/")
    with pytest.raises(ValueError):
        normalize_reviewers_list("alice")


def test_schema_two_round_trip(repo: Path) -> None:
    save_flow(
        repo,
        GitFlow(
            flow=GITFLOW,
            main_branch="main",
            develop_branch="develop",
            base_branch="develop",
            reviewers=("alice", "acme/team"),
            configured=True,
        ),
    )
    flow = load_flow(repo)
    assert flow.reviewers == ("alice", "acme/team")
    raw = git_config_path(repo).read_text(encoding="utf-8")
    assert "schema_version: 2" in raw


def test_schema_one_loads_without_reviewers(repo: Path) -> None:
    path = git_config_path(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "schema_version: 1\nflow: gitflow\nbase_branch: develop\non_done: pr\n",
        encoding="utf-8",
    )
    assert load_flow(repo).reviewers == ()


def test_reviewer_add_and_list(repo: Path, monkeypatch) -> None:
    _gitflow(repo)
    monkeypatch.chdir(repo)
    result = runner.invoke(app, ["branch", "reviewer", "add", "alice", "acme/team"], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    assert load_flow(repo).reviewers == ("alice", "acme/team")
    listed = runner.invoke(app, ["branch", "reviewer", "list"], catch_exceptions=False)
    assert "alice" in listed.stdout
    assert "acme/team" in listed.stdout


def test_reviewer_add_idempotent(repo: Path, monkeypatch) -> None:
    _gitflow(repo)
    monkeypatch.chdir(repo)
    runner.invoke(app, ["branch", "reviewer", "add", "Alice"], catch_exceptions=False)
    result = runner.invoke(app, ["branch", "reviewer", "add", "alice"], catch_exceptions=False)
    assert result.exit_code == 0
    assert "Already configured" in result.output
    assert load_flow(repo).reviewers == ("Alice",)


def test_reviewer_remove_unknown_fails_without_change(repo: Path, monkeypatch) -> None:
    _gitflow(repo)
    save_flow(
        repo,
        GitFlow(
            flow=GITFLOW,
            main_branch="main",
            develop_branch="develop",
            base_branch="develop",
            reviewers=("bob",),
            configured=True,
        ),
    )
    monkeypatch.chdir(repo)
    result = runner.invoke(app, ["branch", "reviewer", "remove", "alice"], catch_exceptions=False)
    assert result.exit_code == 1
    assert load_flow(repo).reviewers == ("bob",)


def test_reviewer_remove_multiple(repo: Path, monkeypatch) -> None:
    _gitflow(repo)
    save_flow(
        repo,
        GitFlow(
            flow=GITFLOW,
            main_branch="main",
            develop_branch="develop",
            base_branch="develop",
            reviewers=("alice", "bob", "acme/team"),
            configured=True,
        ),
    )
    monkeypatch.chdir(repo)
    result = runner.invoke(
        app, ["branch", "reviewer", "remove", "alice", "acme/team"], catch_exceptions=False
    )
    assert result.exit_code == 0
    assert load_flow(repo).reviewers == ("bob",)


def test_reviewer_before_init_fails(repo: Path, monkeypatch) -> None:
    monkeypatch.chdir(repo)
    result = runner.invoke(app, ["branch", "reviewer", "list"], catch_exceptions=False)
    assert result.exit_code == 1
    assert "kcia init" in result.output


def test_branch_config_shows_reviewers(repo: Path, monkeypatch) -> None:
    _gitflow(repo)
    save_flow(
        repo,
        GitFlow(
            flow=GITFLOW,
            main_branch="main",
            develop_branch="develop",
            base_branch="develop",
            reviewers=("alice",),
            configured=True,
        ),
    )
    monkeypatch.chdir(repo)
    result = runner.invoke(app, ["branch", "config"], catch_exceptions=False)
    assert "reviewers: alice" in result.stdout


def test_merge_mode_add_shows_inactive_hint(repo: Path, monkeypatch) -> None:
    _gitflow(repo, on_done=ON_DONE_MERGE)
    monkeypatch.chdir(repo)
    result = runner.invoke(app, ["branch", "reviewer", "add", "alice"], catch_exceptions=False)
    assert result.exit_code == 0
    assert "on_done: pr" in result.output


def test_save_base_branch_preserves_reviewers(repo: Path) -> None:
    _gitflow(repo)
    save_flow(
        repo,
        GitFlow(
            flow=GITFLOW,
            main_branch="main",
            develop_branch="develop",
            base_branch="develop",
            reviewers=("alice",),
            configured=True,
        ),
    )
    save_base_branch(repo, "main")
    assert load_flow(repo).reviewers == ("alice",)
    assert load_flow(repo).base_branch == "main"


def test_malformed_yaml_reviewers_raises(repo: Path) -> None:
    path = git_config_path(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "schema_version: 2\nflow: gitflow\nbase_branch: develop\nreviewers: alice\n",
        encoding="utf-8",
    )
    with pytest.raises(GitFlowConfigError):
        load_flow(repo)
