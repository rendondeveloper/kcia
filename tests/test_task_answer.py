"""`work answer` records injections and retries blocked waves by default."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from kcia.main import app
from kcia.providers.base import RunResult
from kcia.waves.runner import WaveBlocked, WaveResult, run_wave
from kcia.waves.session import Session

runner = CliRunner()


@pytest.fixture()
def git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    return repo


def _block_understanding(repo: Path) -> None:
    session = Session.load(repo)
    with pytest.raises(WaveBlocked):
        run_wave(
            "understanding",
            session,
            provider_runner=lambda *_args, **_kwargs: RunResult(
                output_text="BLOCKED: Which screen?",
                exit_code=0,
            ),
        )


def test_task_answer_retries_blocked_wave(git_repo: Path, monkeypatch) -> None:
    Session.create(git_repo, text="fix the overflow", mode="prompt")
    _block_understanding(git_repo)
    monkeypatch.chdir(git_repo)

    completed = WaveResult(wave_id="understanding", status="completed")
    with patch("kcia.commands.work.retry_wave", return_value=completed) as mock_retry:
        result = runner.invoke(app, ["work", "answer", "The profile screen."])

    assert result.exit_code == 0, result.stdout
    mock_retry.assert_called_once()
    assert mock_retry.call_args[0][1] == "understanding"
    assert mock_retry.call_args.kwargs["on_event"] is not None
    assert mock_retry.call_args.kwargs["on_wave_start"] is not None
    assert mock_retry.call_args.kwargs["should_cancel"] is not None
    assert "completed on retry" in result.stdout
    session = Session.load(git_repo)
    assert session.data["injections"] == ["The profile screen."]


def test_task_answer_without_blocked_wave_prints_note(git_repo: Path, monkeypatch) -> None:
    Session.create(git_repo, text="fix the overflow", mode="prompt")
    monkeypatch.chdir(git_repo)

    with patch("kcia.commands.work.retry_wave") as mock_retry:
        result = runner.invoke(app, ["work", "answer", "Extra context."])

    assert result.exit_code == 0, result.stdout
    mock_retry.assert_not_called()
    assert "No wave is blocked" in result.stdout
    session = Session.load(git_repo)
    assert session.data["injections"] == ["Extra context."]


def test_task_answer_no_retry_preserves_record_only(git_repo: Path, monkeypatch) -> None:
    Session.create(git_repo, text="fix the overflow", mode="prompt")
    _block_understanding(git_repo)
    monkeypatch.chdir(git_repo)

    with patch("kcia.commands.work.retry_wave") as mock_retry:
        result = runner.invoke(
            app,
            ["work", "answer", "--no-retry", "The profile screen."],
        )

    assert result.exit_code == 0, result.stdout
    mock_retry.assert_not_called()
    assert result.stdout.strip() == "Injection recorded."
    session = Session.load(git_repo)
    assert session.data["injections"] == ["The profile screen."]


def test_task_answer_rejects_whitespace_only_text(git_repo: Path, monkeypatch) -> None:
    Session.create(git_repo, text="fix the overflow", mode="prompt")
    monkeypatch.chdir(git_repo)

    with patch("kcia.commands.work.retry_wave") as mock_retry:
        result = runner.invoke(app, ["work", "answer", "  \t\n  "])

    assert result.exit_code == 1, result.stdout
    mock_retry.assert_not_called()
    assert "Answer text is empty." in result.stdout
    session = Session.load(git_repo)
    assert session.data["injections"] == []


def test_task_answer_strips_surrounding_whitespace(git_repo: Path, monkeypatch) -> None:
    Session.create(git_repo, text="fix the overflow", mode="prompt")
    monkeypatch.chdir(git_repo)

    result = runner.invoke(
        app,
        ["work", "answer", "--no-retry", "  Extra   context\n"],
    )

    assert result.exit_code == 0, result.stdout
    session = Session.load(git_repo)
    assert session.data["injections"] == ["Extra   context"]


def test_task_answer_reads_file(git_repo: Path, monkeypatch) -> None:
    Session.create(git_repo, text="fix the overflow", mode="prompt")
    monkeypatch.chdir(git_repo)
    spec = "# Spec\n\n1. Keep lists\n2. Keep `paths`"
    path = git_repo / "answer.md"
    path.write_text(f"\n{spec}\n", encoding="utf-8")

    result = runner.invoke(
        app,
        ["work", "answer", "--no-retry", "--file", str(path)],
    )

    assert result.exit_code == 0, result.stdout
    session = Session.load(git_repo)
    assert session.data["injections"] == [spec]


def test_task_answer_reads_stdin(git_repo: Path, monkeypatch) -> None:
    Session.create(git_repo, text="fix the overflow", mode="prompt")
    monkeypatch.chdir(git_repo)
    spec = "# Spec\n\n---\n\nKeep blank lines."

    result = runner.invoke(
        app,
        ["work", "answer", "--no-retry", "--stdin"],
        input=f"\n{spec}\n",
    )

    assert result.exit_code == 0, result.stdout
    session = Session.load(git_repo)
    assert session.data["injections"] == [spec]


def test_task_answer_rejects_file_and_positional(git_repo: Path, monkeypatch) -> None:
    Session.create(git_repo, text="fix the overflow", mode="prompt")
    monkeypatch.chdir(git_repo)
    path = git_repo / "answer.md"
    path.write_text("from file", encoding="utf-8")

    result = runner.invoke(
        app,
        ["work", "answer", "--no-retry", "--file", str(path), "also positional"],
    )

    assert result.exit_code == 1, result.stdout
    assert "Pass only one of" in result.stdout
    session = Session.load(git_repo)
    assert session.data["injections"] == []


def test_task_answer_rejects_empty_file(git_repo: Path, monkeypatch) -> None:
    Session.create(git_repo, text="fix the overflow", mode="prompt")
    monkeypatch.chdir(git_repo)
    path = git_repo / "empty.md"
    path.write_text("  \n", encoding="utf-8")

    result = runner.invoke(app, ["work", "answer", "--file", str(path)])

    assert result.exit_code == 1, result.stdout
    assert "Answer text is empty." in result.stdout
    session = Session.load(git_repo)
    assert session.data["injections"] == []


def test_work_retry_wires_progress_into_retry_wave(git_repo: Path, monkeypatch) -> None:
    Session.create(git_repo, text="fix the overflow", mode="prompt")
    monkeypatch.chdir(git_repo)

    completed = WaveResult(wave_id="understanding", status="completed")
    with patch("kcia.commands.work.retry_wave", return_value=completed) as mock_retry:
        result = runner.invoke(app, ["work", "retry"])

    assert result.exit_code == 0, result.stdout
    mock_retry.assert_called_once()
    assert mock_retry.call_args[0][1] == "understanding"
    assert mock_retry.call_args.kwargs["on_event"] is not None
    assert mock_retry.call_args.kwargs["on_wave_start"] is not None
    assert mock_retry.call_args.kwargs["should_cancel"] is not None
    assert "completed on retry" in result.stdout
