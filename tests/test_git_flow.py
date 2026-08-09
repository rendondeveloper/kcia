"""Git-flow: base branch detection and branch naming."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from kcia.git.flow import branch_name, detect_base_branch, save_base_branch, slugify


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
    return root


def test_current_branch_is_the_base_when_it_is_a_convention(repo: Path) -> None:
    detected = detect_base_branch(repo)
    assert (detected.name, detected.certain) == ("main", True)


def test_develop_wins_when_it_is_the_only_convention(repo: Path) -> None:
    git(repo, "branch", "develop")
    git(repo, "branch", "-m", "main", "trunk")  # no main/master left
    git(repo, "checkout", "-b", "feature-x")
    detected = detect_base_branch(repo)
    assert (detected.name, detected.certain) == ("develop", True)


def test_ambiguous_when_several_conventions_and_a_feature_branch(repo: Path) -> None:
    git(repo, "branch", "develop")
    git(repo, "checkout", "-b", "feature/x")
    detected = detect_base_branch(repo)
    assert detected.certain is False
    assert "develop" in detected.candidates and "main" in detected.candidates


def test_no_convention_at_all_is_ambiguous(repo: Path) -> None:
    git(repo, "branch", "-m", "main", "trunk")
    git(repo, "checkout", "-b", "feature/x")
    detected = detect_base_branch(repo)
    assert detected.certain is False
    assert "no `develop`, `main` or `master`" in detected.reason


def test_a_remembered_answer_is_reused(repo: Path) -> None:
    git(repo, "branch", "develop")
    git(repo, "checkout", "-b", "feature/x")
    save_base_branch(repo, "develop")
    detected = detect_base_branch(repo)
    assert (detected.name, detected.certain) == ("develop", True)


def test_a_remembered_branch_that_no_longer_exists_is_ignored(repo: Path) -> None:
    save_base_branch(repo, "gone")
    assert detect_base_branch(repo).name == "main"


def test_branch_names_follow_git_flow() -> None:
    assert (
        branch_name("feat", ticket="ip-116", subject="Add the commit flow")
        == "feature/IP-116-add-the-commit-flow"
    )
    assert branch_name("fix", ticket=None, subject="overflow en el header") == "fix/overflow-en-el-header"
    assert branch_name("docs", ticket="IP-1", subject="README") == "docs/IP-1-readme"


def test_cli_creates_the_branch_from_the_chosen_base(repo: Path, monkeypatch) -> None:
    from typer.testing import CliRunner

    from kcia.main import app
    from kcia.waves.session import Session

    Session.create(repo, text="IP-116", mode="ticket", ticket_key="IP-116", title="add the flow")
    git(repo, "branch", "develop")
    monkeypatch.chdir(repo)
    result = CliRunner().invoke(app, ["branch", "start", "--base", "develop", "--yes"])
    assert result.exit_code == 0, result.output
    assert "On branch feature/IP-116-add-the-flow" in result.output
    assert Session.load(repo).task["base_branch"] == "develop"


def test_cli_refuses_to_guess_the_base_without_a_tty(repo: Path, monkeypatch) -> None:
    from typer.testing import CliRunner

    from kcia.main import app

    git(repo, "branch", "develop")
    git(repo, "checkout", "-b", "feature/x")
    monkeypatch.chdir(repo)
    result = CliRunner().invoke(app, ["branch", "start", "add a loader"])
    assert result.exit_code == 1
    assert "--base" in result.output


def test_cli_asks_and_remembers_the_base(repo: Path, monkeypatch) -> None:
    from typer.testing import CliRunner

    from kcia.git.flow import load_flow
    from kcia.main import app

    git(repo, "branch", "develop")
    git(repo, "checkout", "-b", "feature/x")
    monkeypatch.chdir(repo)
    monkeypatch.setattr("kcia.commands.branch.interactive", lambda: True)
    result = CliRunner().invoke(app, ["branch", "start", "add a loader"], input="1\ny\n")
    assert result.exit_code == 0, result.output
    assert "1. develop" in result.output
    assert load_flow(repo).base_branch == "develop"


def test_slug_is_bounded() -> None:
    slug = slugify("a very long sentence with a great many words indeed in it")
    assert len(slug) <= 48
    assert slug.count("-") <= 5
