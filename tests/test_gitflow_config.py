"""The branching model is decided at init and applied without asking."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from kcia.git.autobranch import ensure_task_branch
from kcia.git.cycle import is_cycle_open
from kcia.git.flow import (
    CURRENT_BRANCH,
    GITFLOW,
    GitFlow,
    detect_branches,
    load_flow,
    save_flow,
)
from kcia.git.repo import current_branch
from kcia.waves.session import Session


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


def gitflow(repo: Path, base: str = "develop") -> None:
    save_flow(
        repo,
        GitFlow(
            flow=GITFLOW,
            main_branch="main",
            develop_branch=base,
            base_branch=base,
            configured=True,
        ),
    )


def test_detection_reads_both_roles_off_the_real_branches(repo: Path) -> None:
    main, develop = detect_branches(repo)
    assert (main.name, main.certain) == ("main", True)
    assert (develop.name, develop.certain) == ("develop", True)


def test_two_main_candidates_are_not_certain(repo: Path) -> None:
    git(repo, "branch", "master")
    main, _ = detect_branches(repo)
    assert main.certain is False
    assert set(main.candidates) == {"main", "master"}


def test_a_missing_role_is_reported_as_absent(tmp_path: Path) -> None:
    root = tmp_path / "bare"
    root.mkdir()
    git(root, "init", "--initial-branch=main")
    git(root, "config", "user.email", "t@e.com")
    git(root, "config", "user.name", "T")
    (root / "a.txt").write_text("a\n", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-m", "c")
    _, develop = detect_branches(root)
    assert develop.name is None


def test_config_round_trips(repo: Path) -> None:
    gitflow(repo)
    flow = load_flow(repo)
    assert (flow.flow, flow.base_branch, flow.main_branch) == (GITFLOW, "develop", "main")
    assert flow.uses_gitflow is True
    assert flow.on_done == "pr"


def test_an_older_config_with_only_a_base_still_reads_as_gitflow(repo: Path) -> None:
    path = repo / ".ai" / "local" / "git.yaml"
    path.parent.mkdir(parents=True)
    path.write_text("schema_version: 1\nbase_branch: develop\n", encoding="utf-8")
    flow = load_flow(repo)
    assert flow.uses_gitflow is True
    assert flow.on_done == "pr"


def test_gitflow_opens_the_branch_without_asking(repo: Path) -> None:
    gitflow(repo)
    session = Session.create(
        repo, text="IP-116", mode="ticket", ticket_key="IP-116", title="corrige el overflow"
    )
    outcome = ensure_task_branch(session)
    assert outcome is not None and outcome.created
    assert current_branch(repo) == "fix/IP-116-corrige-el-overflow"
    assert Session.load(repo).task["branch"] == "fix/IP-116-corrige-el-overflow"
    assert is_cycle_open(repo)


def test_the_branch_is_opened_once_per_task(repo: Path) -> None:
    gitflow(repo)
    session = Session.create(repo, text="add a loader", mode="prompt", title="add a loader")
    ensure_task_branch(session)
    assert ensure_task_branch(session) is None  # nothing to say the second time


def test_without_gitflow_nothing_touches_git(repo: Path) -> None:
    save_flow(repo, GitFlow(flow=CURRENT_BRANCH, main_branch="main", configured=True))
    session = Session.create(repo, text="add a loader", mode="prompt", title="add a loader")
    assert ensure_task_branch(session) is None
    assert current_branch(repo) == "main"


def test_an_unconfigured_repo_is_left_alone(repo: Path) -> None:
    session = Session.create(repo, text="add a loader", mode="prompt", title="add a loader")
    assert ensure_task_branch(session) is None
    assert current_branch(repo) == "main"


def test_a_missing_base_branch_warns_and_keeps_going(repo: Path) -> None:
    gitflow(repo, base="nope")
    session = Session.create(repo, text="add a loader", mode="prompt", title="add a loader")
    outcome = ensure_task_branch(session)
    assert outcome is not None and not outcome.created
    assert "does not exist" in outcome.message
    assert current_branch(repo) == "main"


def test_an_existing_branch_name_is_not_adopted(repo: Path) -> None:
    gitflow(repo)
    git(repo, "branch", "feature/add-a-loader")
    session = Session.create(repo, text="add a loader", mode="prompt", title="add a loader")
    outcome = ensure_task_branch(session)
    assert outcome is not None and not outcome.created
    assert "already exists" in outcome.message
    assert current_branch(repo) == "main"


def test_standing_on_the_wrong_branch_warns_instead_of_moving_you(repo: Path) -> None:
    gitflow(repo)
    session = Session.create(repo, text="add a loader", mode="prompt", title="add a loader")
    ensure_task_branch(session)
    git(repo, "checkout", "main")
    outcome = ensure_task_branch(Session.load(repo))
    assert outcome is not None and not outcome.created
    assert "Nothing was changed" in outcome.message


def _init(repo: Path, monkeypatch, *args: str, stdin: str = "") -> str:
    """Run `kcia init` in-process, pretending there is a terminal when input is given."""
    from typer.testing import CliRunner

    from kcia.main import app

    monkeypatch.chdir(repo)
    monkeypatch.setattr("kcia.commands.init.interactive", lambda: bool(stdin))
    result = CliRunner().invoke(app, ["init", *args], input=stdin)
    assert result.exit_code == 0, result.output
    return result.output


@pytest.fixture()
def project(repo: Path) -> Path:
    """A repo with something detectable in it, so `kcia init` gets that far."""
    (repo / "pubspec.yaml").write_text(
        "name: demo\nenvironment:\n  sdk: '>=3.0.0 <4.0.0'\n", encoding="utf-8"
    )
    return repo


def test_init_asks_once_and_records_the_answer(project: Path, monkeypatch) -> None:
    output = _init(project, monkeypatch, stdin="1\nmain\ndevelop\n1\n")
    assert "1. Git flow" in output
    flow = load_flow(project)
    assert (flow.flow, flow.base_branch, flow.main_branch) == (GITFLOW, "develop", "main")
    assert flow.on_done == "pr"


def test_init_can_be_told_to_stay_on_the_current_branch(project: Path, monkeypatch) -> None:
    output = _init(project, monkeypatch, stdin="2\n")
    assert "No git flow" in output
    assert load_flow(project).uses_gitflow is False


def test_init_does_not_ask_again_once_configured(project: Path, monkeypatch) -> None:
    _init(project, monkeypatch, "--yes")
    output = _init(project, monkeypatch, "--yes")
    assert "Already configured" in output


def test_flags_skip_the_dialog(project: Path, monkeypatch) -> None:
    _init(project, monkeypatch, "--gitflow", "--main-branch", "main", "--develop-branch", "develop")
    assert load_flow(project).base_branch == "develop"
    _init(project, monkeypatch, "--no-gitflow")
    assert load_flow(project).uses_gitflow is False


def test_init_asks_on_done_after_gitflow(project: Path, monkeypatch) -> None:
    output = _init(project, monkeypatch, stdin="1\nmain\ndevelop\n2\n")
    assert "When `kcia done` finishes" in output
    assert load_flow(project).on_done == "merge"


def test_init_on_done_flag_skips_the_question(project: Path, monkeypatch) -> None:
    _init(
        project,
        monkeypatch,
        "--gitflow",
        "--main-branch",
        "main",
        "--develop-branch",
        "develop",
        "--on-done",
        "merge",
    )
    assert load_flow(project).on_done == "merge"


def test_init_on_done_flag_updates_an_existing_gitflow_repo(
    project: Path, monkeypatch
) -> None:
    _init(project, monkeypatch, "--yes")
    assert load_flow(project).on_done == "pr"
    output = _init(project, monkeypatch, "--on-done", "merge")
    assert load_flow(project).on_done == "merge"
    assert load_flow(project).uses_gitflow is True
    assert "merge into" in output


def test_init_on_done_flag_is_rejected_without_gitflow(
    project: Path, monkeypatch
) -> None:
    from typer.testing import CliRunner

    from kcia.main import app

    monkeypatch.chdir(project)
    monkeypatch.setattr("kcia.commands.init.interactive", lambda: False)
    result = CliRunner().invoke(app, ["init", "--no-gitflow", "--on-done", "merge"])
    assert result.exit_code == 1
    assert "only applies when git flow is on" in result.output


def test_non_interactive_turns_it_on_only_when_a_develop_branch_exists(
    project: Path, monkeypatch
) -> None:
    _init(project, monkeypatch, "--yes")
    assert load_flow(project).uses_gitflow is True

    git(project, "branch", "-D", "develop")
    (project / ".ai" / "local" / "git.yaml").unlink()
    _init(project, monkeypatch, "--yes")
    assert load_flow(project).uses_gitflow is False
