"""A wave that declares itself blocked must stop the run."""

from __future__ import annotations

from pathlib import Path

import pytest

from kcia.providers.base import RunResult
from kcia.waves.blocked import (
    detect_blocked,
    format_premature_block_retry,
    should_honour_block,
)
from kcia.waves.runner import WaveBlocked, run_wave
from kcia.waves.session import Session, context_dir, runs_dir


@pytest.fixture()
def git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    return repo


@pytest.fixture()
def session(git_repo: Path) -> Session:
    Session.create(git_repo, text="fix the overflow", mode="prompt")
    return Session.load(git_repo)


def _runner(text: str, *, tool_calls: int = 0):
    def run(*_args, **_kwargs):
        return RunResult(output_text=text, exit_code=0, tool_calls=tool_calls)

    return run


# --- detection ---------------------------------------------------------------


def test_detects_the_protocol_marker() -> None:
    assert detect_blocked("BLOCKED: Which screen?") == "Which screen?"


def test_detects_the_marker_through_markdown_decoration() -> None:
    for line in ("**BLOCKED:** Which screen?", "- BLOCKED: Which screen?", "> **BLOCKED**: Which screen?"):
        assert detect_blocked(line) == "Which screen?", line


def test_detects_pre_protocol_shapes() -> None:
    """The shapes agents produced before the protocol existed."""
    assert detect_blocked("**Open question (blocking):** What is the change request?")
    assert detect_blocked("**Status:** `UNKNOWN` — the wave started without a work item")


def test_normal_output_is_not_blocked() -> None:
    assert detect_blocked("# Plan\n\n1. Fix the overflow\n2. Add a test\n") is None
    assert detect_blocked("") is None


def test_passing_mentions_do_not_trigger() -> None:
    """A false positive halts a healthy run, so detection stays narrow."""
    assert detect_blocked("The build is not blocked by this change.") is None
    assert detect_blocked("Coverage for legacy/util.dart is UNKNOWN, so I added tests.") is None
    assert detect_blocked("## Open questions\n\n- Should the loader be dismissible?") is None
    assert detect_blocked("I considered marking this BLOCKED but proceeded instead.") is None


def test_long_reasons_are_truncated() -> None:
    reason = detect_blocked("BLOCKED: " + "x" * 900)
    assert reason is not None and len(reason) <= 500


def test_should_honour_block_requires_tool_use_or_retry() -> None:
    assert should_honour_block(tool_calls=1, premature_retries=0) is True
    assert should_honour_block(tool_calls=0, premature_retries=0) is False
    assert should_honour_block(tool_calls=0, premature_retries=1) is True


def test_format_premature_block_retry_names_plan_section(git_repo: Path) -> None:
    context = git_repo / ".ai" / "context"
    context.mkdir(parents=True)
    (context / "plan.md").write_text(
        "## Decisions on open questions\n\nDTO leakage — option (b).\n",
        encoding="utf-8",
    )
    message = format_premature_block_retry("Confirm the DTO leakage approach", git_repo)
    assert "without using any tools" in message
    assert "Decisions on open questions" in message


# --- runner integration ------------------------------------------------------


def test_run_wave_raises_and_records_the_reason(session: Session) -> None:
    with pytest.raises(WaveBlocked) as excinfo:
        run_wave(
            "understanding",
            session,
            provider_runner=_runner("BLOCKED: Which screen?", tool_calls=1),
        )

    assert excinfo.value.reason == "Which screen?"
    reloaded = Session.load(session.repo_root)
    assert reloaded.wave_status("understanding") == "blocked"
    assert reloaded.waves["understanding"]["blocked_reason"] == "Which screen?"


def test_blocked_output_never_reaches_the_context_files(session: Session) -> None:
    """task.md is read by every later wave; a question must not land there."""
    with pytest.raises(WaveBlocked):
        run_wave(
            "understanding",
            session,
            provider_runner=_runner("BLOCKED: Which screen?", tool_calls=1),
        )

    assert not (context_dir(session.repo_root) / "task.md").exists()


def test_blocked_response_is_kept_for_inspection(session: Session) -> None:
    with pytest.raises(WaveBlocked) as excinfo:
        run_wave(
            "understanding",
            session,
            provider_runner=_runner(
                "BLOCKED: Which screen?\n\nI checked lib/ first.",
                tool_calls=1,
            ),
        )

    path = excinfo.value.output_path
    assert path is not None and path.is_file()
    assert path.parent == runs_dir(session.repo_root)
    assert "I checked lib/ first." in path.read_text(encoding="utf-8")


def test_blocked_is_not_recorded_as_a_failure(session: Session) -> None:
    with pytest.raises(WaveBlocked):
        run_wave(
            "understanding",
            session,
            provider_runner=_runner("BLOCKED: Which screen?", tool_calls=1),
        )

    state = Session.load(session.repo_root).waves["understanding"]
    assert state["status"] == "blocked"
    assert "error" not in state


def test_the_lock_is_released_so_the_wave_can_be_retried(session: Session) -> None:
    with pytest.raises(WaveBlocked):
        run_wave(
            "understanding",
            session,
            provider_runner=_runner("BLOCKED: Which screen?", tool_calls=1),
        )

    reloaded = Session.load(session.repo_root)
    assert not reloaded.is_locked()


def test_retry_after_an_answer_completes_the_wave(session: Session) -> None:
    with pytest.raises(WaveBlocked):
        run_wave(
            "understanding",
            session,
            provider_runner=_runner("BLOCKED: Which screen?", tool_calls=1),
        )

    session = Session.load(session.repo_root)
    session.add_injection("The profile screen.")
    session.set_wave_status("understanding", "pending")
    session.save()

    result = run_wave(
        "understanding", session, provider_runner=_runner("# Task\n\nOverflow on profile.\n")
    )
    assert result.status == "completed"
    assert "Overflow on profile." in (
        context_dir(session.repo_root) / "task.md"
    ).read_text(encoding="utf-8")


def test_the_protocol_is_present_in_every_wave_prompt(melos_session) -> None:
    from kcia.waves.definitions import load_waves
    from kcia.waves.prompts import build_prompt

    for wave in load_waves():
        assert "BLOCKED:" in build_prompt(wave, melos_session), wave.id


def test_premature_block_retries_once(session: Session) -> None:
    calls: list[int] = []

    def run(*_args, **_kwargs):
        calls.append(1)
        if len(calls) == 1:
            return RunResult(output_text="BLOCKED: Which screen?", exit_code=0, tool_calls=0)
        return RunResult(output_text="# Task\n\nOverflow on profile.\n", exit_code=0)

    result = run_wave("understanding", session, provider_runner=run)
    assert result.status == "completed"
    assert len(calls) == 2


def test_premature_block_surfaces_after_retry(session: Session) -> None:
    calls: list[int] = []

    def run(*_args, **_kwargs):
        calls.append(1)
        return RunResult(output_text="BLOCKED: Which screen?", exit_code=0, tool_calls=0)

    with pytest.raises(WaveBlocked) as excinfo:
        run_wave("understanding", session, provider_runner=run)

    assert excinfo.value.tool_calls == 0
    assert len(calls) == 2
    reloaded = Session.load(session.repo_root)
    assert reloaded.wave_status("understanding") == "blocked"


def test_blocked_with_tool_calls_is_honoured_immediately(session: Session) -> None:
    calls: list[int] = []

    def run(*_args, **_kwargs):
        calls.append(1)
        return RunResult(
            output_text="BLOCKED: Contradiction in lib/foo.dart",
            exit_code=0,
            tool_calls=2,
        )

    with pytest.raises(WaveBlocked):
        run_wave("understanding", session, provider_runner=run)

    assert len(calls) == 1
