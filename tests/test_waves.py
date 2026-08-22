"""Wave engine tests — Fase 3 acceptance criteria."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from typer.testing import CliRunner

from kcia.main import app

from kcia.providers.base import RunResult
from kcia.waves.definitions import load_waves
from kcia.waves.prompts import build_prompt
from kcia.waves.runner import _format_validation_failures, check_requires, run_wave
from kcia.waves.session import Session, classify_input, session_path
from kcia.waves.validation import (
    ValidationFailure,
    ValidationReport,
    ValidationStep,
    build_validation_plan,
    matches_empty_suite,
    run_validation,
)

ROOT = Path(__file__).resolve().parents[1]
KCIA = ROOT / ".venv" / "bin" / "kcia"
MELOS_REPO = ROOT / "tests" / "fixtures" / "repos" / "melos_mono"
runner = CliRunner()


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    return repo


def _mock_provider(*_args, **_kwargs) -> RunResult:
    return RunResult(output_text="# Task output\n\nProblem understood.\n", exit_code=0)


def test_classify_input_prompt_mode_without_jira() -> None:
    assert classify_input("arregla el overflow", {}) == "prompt"


def test_work_creates_session_json(git_repo: Path, monkeypatch) -> None:
    monkeypatch.chdir(git_repo)
    with patch("kcia.commands.work._execute"):
        result = runner.invoke(app, ["work", "arregla el overflow"])
    assert result.exit_code == 0, result.stdout
    path = session_path(git_repo)
    assert path.is_file()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["task"]["mode"] == "prompt"
    assert data["task"]["prompt"] == "arregla el overflow"


def test_work_list_shows_five_waves(git_repo: Path) -> None:
    Session.create(git_repo, text="demo task", mode="prompt")
    result = subprocess.run(
        [str(KCIA), "work", "list"],
        capture_output=True,
        text=True,
        cwd=git_repo,
    )
    assert result.returncode == 0
    lines = [line for line in result.stdout.strip().splitlines() if line]
    assert len(lines) == 5
    assert "understanding" in result.stdout
    assert "implementation" in result.stdout


def test_wave_run_understanding_writes_task_md(git_repo: Path) -> None:
    Session.create(git_repo, text="fix bug", mode="prompt")
    wave = load_waves()[0]
    session = Session.load(git_repo)

    with patch("kcia.waves.runner.get_adapter") as mock_get_adapter:
        adapter = mock_get_adapter.return_value
        adapter.locate.return_value = "/usr/bin/true"
        adapter.capabilities.supports_streaming = False
        result = run_wave(
            wave.id,
            session,
            provider_runner=lambda *_a, **_k: RunResult(
                output_text="# Task\n\nScoped work.",
                exit_code=0,
            ),
        )

    assert result.status == "completed"
    task_md = git_repo / ".ai" / "context" / "task.md"
    assert task_md.is_file()
    assert "Scoped work" in task_md.read_text(encoding="utf-8")


def test_wave_run_analysis_requires_understanding(git_repo: Path) -> None:
    Session.create(git_repo, text="fix bug", mode="prompt")
    session = Session.load(git_repo)
    with pytest.raises(RuntimeError, match="requires completed waves: understanding"):
        check_requires(session, load_waves()[1])


def test_implementation_prompt_includes_plan(git_repo: Path) -> None:
    Session.create(git_repo, text="fix bug", mode="prompt")
    session = Session.load(git_repo)
    context = git_repo / ".ai" / "context"
    context.mkdir(parents=True, exist_ok=True)
    (context / "plan.md").write_text("# Plan\n\nDo the thing.\n", encoding="utf-8")

    wave = next(w for w in load_waves() if w.id == "implementation")
    prompt = build_prompt(wave, session)
    assert "Do the thing." in prompt


def test_validation_plan_multi_profile_melos(git_repo: Path) -> None:
    manifest = {
        "schema_version": 2,
        "project": {"name": "melos", "default_profile": "backend-dart"},
        "profiles": [
            {
                "id": "mobile-flutter",
                "roots": ["packages/app_mobile/**"],
                "commands": {},
            },
            {
                "id": "backend-dart",
                "roots": ["packages/api/**"],
                "commands": {},
            },
        ],
        "dependencies": [],
    }
    (git_repo / ".ai").mkdir(parents=True)
    (git_repo / ".ai" / "manifest.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False),
        encoding="utf-8",
    )
    session = Session.create(
        git_repo,
        text="change",
        mode="prompt",
        active_profiles=["mobile-flutter", "backend-dart"],
    )
    from kcia.profiles.schema import Manifest

    plan = build_validation_plan(
        session,
        Manifest.model_validate(manifest),
        touched=[git_repo / "packages"],
        repo_root=git_repo,
    )
    profile_ids = {step.profile_id for step in plan}
    assert "mobile-flutter" in profile_ids
    assert "backend-dart" in profile_ids
    mobile_test = next(s for s in plan if s.profile_id == "mobile-flutter" and s.command_name == "test")
    backend_test = next(s for s in plan if s.profile_id == "backend-dart" and s.command_name == "test")
    assert "flutter test" in mobile_test.command
    assert "dart test" in backend_test.command


def test_validation_retries_only_failed_profile() -> None:
    plan = [
        ValidationStep("a", Path("/tmp"), "lint", "false"),
        ValidationStep("b", Path("/tmp"), "lint", "true"),
    ]
    with patch("kcia.waves.validation.subprocess.run") as mock_run:
        mock_run.side_effect = [
            subprocess.CompletedProcess(args="", returncode=1, stdout="", stderr="fail a"),
            subprocess.CompletedProcess(args="", returncode=0, stdout="", stderr=""),
            subprocess.CompletedProcess(args="", returncode=1, stdout="", stderr="fail a"),
        ]
        report = run_validation(plan, retry_limit=2)
    assert not report.success
    assert report.failures[0].step.profile_id == "a"
    assert mock_run.call_count == 3


def test_lock_blocks_second_acquire(git_repo: Path) -> None:
    session = Session.create(git_repo, text="x", mode="prompt")
    session.acquire_lock()
    with pytest.raises(RuntimeError, match="wave lock held by pid"):
        session.acquire_lock()
    session.release_lock()


def test_stale_lock_cleared_for_dead_pid(git_repo: Path) -> None:
    session = Session.create(git_repo, text="x", mode="prompt")
    session.data["lock"] = {"pid": 999999999, "acquired_at": "now"}
    session.save()
    assert not session.is_locked()


_EMPTY_SUITE = {
    "command": "test",
    "exit_code": 65,
    "output_contains": ["No test files were passed"],
}


def test_empty_suite_signature_matches_dart_test_output() -> None:
    step = ValidationStep(
        "backend-dart",
        Path("/tmp/pkg"),
        "test",
        "dart test",
        empty_suite_signature=_EMPTY_SUITE,
    )
    missing = ValidationFailure(
        step=step,
        exit_code=65,
        output='No test files were passed and the default "test/" directory doesn\'t exist.',
    )
    real_failure = ValidationFailure(
        step=step,
        exit_code=1,
        output="Expected: true\n  Actual: false",
    )
    assert matches_empty_suite(missing)
    assert not matches_empty_suite(real_failure)


def test_format_validation_failures_rewrites_empty_suite_as_scaffold_instruction() -> None:
    step = ValidationStep(
        "backend-dart",
        Path("/tmp/pkg"),
        "test",
        "dart test",
        empty_suite_signature=_EMPTY_SUITE,
    )
    report = ValidationReport(
        success=False,
        failures=[
            ValidationFailure(
                step=step,
                exit_code=65,
                output='No test files were passed and the default "test/" directory doesn\'t exist.',
            )
        ],
    )
    text = _format_validation_failures(report)
    assert "No test suite exists yet for profile backend-dart" in text
    assert "create one covering the recent changes" in text
    assert "No test files were passed" not in text
