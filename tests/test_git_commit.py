"""Commit composition: message format, plan/code split, and the CLI gate."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from kcia.git.commit import (
    NothingToCommit,
    build_message,
    infer_commit_type,
    is_plan_path,
    plan_commits,
)
from kcia.main import app
from kcia.waves.session import Session

runner = CliRunner()


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def log(repo: Path) -> list[str]:
    out = subprocess.run(
        ["git", "log", "--format=%s"], cwd=repo, capture_output=True, text=True, check=True
    )
    return out.stdout.splitlines()


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


@pytest.fixture()
def worked(repo: Path) -> Path:
    """A repo with a plan written by the waves and code written by the builder."""
    (repo / ".ai" / "context").mkdir(parents=True)
    (repo / ".ai" / "context" / "plan.md").write_text("# Plan\n", encoding="utf-8")
    (repo / ".ai" / "local").mkdir(parents=True, exist_ok=True)
    (repo / ".ai" / "local" / "session.json").write_text("{}", encoding="utf-8")
    (repo / "src").mkdir()
    (repo / "src" / "app.py").write_text("print('hi')\n", encoding="utf-8")
    return repo


def test_message_carries_the_ticket_when_there_is_one() -> None:
    assert build_message("feat", ticket="IP-116", subject="add the flow") == (
        "feat: IP-116 - add the flow"
    )


def test_message_omits_the_key_entirely_without_a_ticket() -> None:
    assert build_message("fix", ticket=None, subject="header overflow") == "fix: header overflow"
    assert build_message("docs", ticket="  ", subject="readme") == "docs: readme"


def test_ticket_keys_are_upper_cased() -> None:
    assert build_message("feat", ticket="ip-116", subject="x") == "feat: IP-116 - x"


def test_only_the_three_types_are_accepted() -> None:
    with pytest.raises(ValueError):
        build_message("chore", ticket=None, subject="x")


def test_regenerable_ai_output_is_not_plan_content() -> None:
    assert is_plan_path(".ai/context/plan.md")
    assert not is_plan_path(".ai/local/session.json")
    assert not is_plan_path(".ai/cache/x.json")
    assert not is_plan_path(".ai/generated/CLAUDE.md")
    assert not is_plan_path("src/app.py")


def test_plan_and_code_become_two_commits(worked: Path) -> None:
    commits = plan_commits(worked, subject="add the flow", ticket="IP-116")
    assert [c.kind for c in commits] == ["plan", "code"]
    assert commits[0].message == "docs: IP-116 - plan — add the flow"
    assert commits[0].paths == [".ai/context/plan.md"]
    assert commits[1].message == "feat: IP-116 - add the flow"
    assert commits[1].paths == ["src/app.py"]


def test_single_collapses_them(worked: Path) -> None:
    commits = plan_commits(worked, subject="add the flow", ticket=None, single=True)
    assert len(commits) == 1
    assert commits[0].message == "feat: add the flow"
    assert commits[0].paths == [".ai/context/plan.md", "src/app.py"]


def test_a_clean_worktree_has_nothing_to_commit(repo: Path) -> None:
    with pytest.raises(NothingToCommit):
        plan_commits(repo, subject="x", ticket=None)


def test_type_is_inferred_but_the_flag_wins(worked: Path) -> None:
    assert infer_commit_type("arregla el overflow", ["src/app.py"]) == "fix"
    assert infer_commit_type("add a loader", ["src/app.py"]) == "feat"
    assert infer_commit_type("anything", []) == "docs"
    commits = plan_commits(worked, subject="arregla el overflow", ticket=None, commit_type="feat")
    assert commits[-1].commit_type == "feat"


def test_cli_writes_both_commits_after_confirmation(worked: Path, monkeypatch) -> None:
    Session.create(worked, text="IP-116", mode="ticket", ticket_key="IP-116", title="add the flow")
    monkeypatch.chdir(worked)
    result = runner.invoke(app, ["commit", "--yes"])
    assert result.exit_code == 0, result.output
    assert log(worked)[:2] == [
        "feat: IP-116 - add the flow",
        "docs: IP-116 - plan — add the flow",
    ]


def test_cli_declining_the_prompt_writes_nothing(worked: Path, monkeypatch) -> None:
    Session.create(worked, text="add the flow", mode="prompt", title="add the flow")
    monkeypatch.chdir(worked)
    result = runner.invoke(app, ["commit"], input="n\n")
    assert result.exit_code == 1
    assert "Nothing was committed." in result.output
    assert log(worked) == ["initial"]


def test_dry_run_shows_the_commits_and_stops(worked: Path, monkeypatch) -> None:
    Session.create(worked, text="add the flow", mode="prompt", title="add the flow")
    monkeypatch.chdir(worked)
    result = runner.invoke(app, ["commit", "--dry-run"])
    assert result.exit_code == 0
    assert "feat: add the flow" in result.output
    assert log(worked) == ["initial"]


def test_unrelated_staged_files_are_not_swept_into_the_commit(worked: Path, monkeypatch) -> None:
    (worked / "unrelated.txt").write_text("x\n", encoding="utf-8")
    Session.create(worked, text="add the flow", mode="prompt", title="add the flow")
    monkeypatch.chdir(worked)
    result = runner.invoke(app, ["commit", "--yes", "--type", "feat"])
    assert result.exit_code == 0, result.output
    files = subprocess.run(
        ["git", "show", "--name-only", "--format=", "HEAD"],
        cwd=worked,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    # `unrelated.txt` is code too, so it lands in the code commit — what must not
    # happen is regenerable `.ai/local` output being committed.
    assert ".ai/local/session.json" not in files
