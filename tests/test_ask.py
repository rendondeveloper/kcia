"""Tests for `kcia ask` and ask prompt composition."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from kcia.ask.conversation import AskConversation, ask_path, build_work_prompt, clear_conversation
from kcia.ask.prompt import build_ask_prompt
from kcia.ask.run import run_ask_turn
from kcia.history import index, log
from kcia.main import app
from kcia.providers.base import RunRequest, RunResult
from kcia.waves.session import Session

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "repos"
runner = CliRunner()


def _repo(tmp_path: Path, name: str) -> Path:
    target = tmp_path / name
    shutil.copytree(FIXTURES / name, target)
    return target


def _init(repo: Path) -> None:
    result = runner.invoke(app, ["init", "--yes", "--path", str(repo)])
    assert result.exit_code == 0, result.output


@pytest.fixture
def melos_repo(tmp_path: Path, monkeypatch) -> Path:
    repo = _repo(tmp_path, "melos_mono")
    _init(repo)
    monkeypatch.chdir(repo)
    return repo


def _fake_runner(reply: str, *, session_id: str = "sess-1", exit_code: int = 0):
    def _run(adapter, req: RunRequest, on_event=None, should_cancel=None):
        return RunResult(
            output_text=reply,
            exit_code=exit_code,
            session_id=session_id,
        )

    return _run


def _log_session(repo: Path, *, title: str = "Fix layout overflow") -> log.SessionEntry:
    entry = log.SessionEntry(
        id=log.new_id(),
        timestamp="2026-08-14T10:00:00Z",
        title=title,
        summary="Reduced header padding to stop the profile screen from overflowing.",
        decisions=[],
        files=[{"path": "packages/app/lib/profile.dart", "change": "modified"}],
        commit_sha=None,
        branch="main",
        task_id=None,
    )
    log.append_entry(repo, entry)
    index.sync(repo, entry)
    return entry


def test_ask_prompt_static_instruction_before_dynamic_sections(melos_repo: Path) -> None:
    _log_session(melos_repo, title="Fix layout overflow")
    prompt = build_ask_prompt(
        melos_repo,
        "layout overflow on profile screen",
        AskConversation(),
        resume=False,
    )
    instruction_pos = prompt.index("## Ask mode")
    repo_map_pos = prompt.index("## Repository map")
    history_pos = prompt.index("## Related history")
    question_pos = prompt.index("## Question")
    assert instruction_pos < repo_map_pos < history_pos < question_pos


def test_ask_prompt_resume_static_instruction_before_dynamic_sections(melos_repo: Path) -> None:
    _log_session(melos_repo, title="Fix layout overflow")
    conversation = AskConversation(
        turns=[],
        provider="cursor",
        model="composer-2.5",
        provider_session_id="sess-1",
    )
    prompt = build_ask_prompt(
        melos_repo,
        "layout overflow follow up",
        conversation,
        resume=True,
    )
    instruction_pos = prompt.index("## Ask mode")
    history_pos = prompt.index("## Related history")
    question_pos = prompt.index("## Question")
    assert instruction_pos < history_pos < question_pos


def test_ask_prompt_includes_history_and_not_profile_references(melos_repo: Path) -> None:
    _log_session(melos_repo, title="Fix layout overflow")
    prompt = build_ask_prompt(
        melos_repo,
        "layout overflow on profile screen",
        AskConversation(),
        resume=False,
    )
    assert "## Related history" in prompt
    assert "Fix layout overflow" in prompt
    assert "Profile bundle:" not in prompt
    assert "## Wave:" not in prompt


def test_ask_prompt_punctuation_does_not_crash(melos_repo: Path) -> None:
    _log_session(melos_repo, title="fix: header (overflow)")
    prompt = build_ask_prompt(
        melos_repo,
        "fix: header (overflow)",
        AskConversation(),
        resume=False,
    )
    assert "fix: header (overflow)" in prompt or "Related history" in prompt


def test_ask_prompt_resume_omits_full_context(melos_repo: Path) -> None:
    conversation = AskConversation(
        turns=[],
        provider="cursor",
        model="composer-2.5",
        provider_session_id="sess-1",
    )
    prompt = build_ask_prompt(
        melos_repo,
        "follow up question",
        conversation,
        resume=True,
    )
    assert "Follow-up" in prompt
    assert "Profile bundle:" not in prompt
    assert "## Prior turns" not in prompt
    assert "follow up question" in prompt


def test_ask_cli_prints_reply(melos_repo: Path) -> None:
    with patch("kcia.commands.ask.check_agents_ready", return_value=[]):
        with patch("kcia.commands.ask.run_ask_turn") as mock_run:
            mock_run.return_value = ("The login flow lives in auth.dart.", AskConversation())
            result = runner.invoke(app, ["ask", "how does login work?"])
    assert result.exit_code == 0, result.output
    assert "The login flow lives in auth.dart." in result.stdout


def test_ask_cli_reads_stdin(melos_repo: Path) -> None:
    with patch("kcia.commands.ask.check_agents_ready", return_value=[]):
        with patch("kcia.commands.ask.run_ask_turn") as mock_run:
            mock_run.return_value = ("ok", AskConversation())
            result = runner.invoke(app, ["ask", "--stdin"], input="question from stdin\n")
    assert result.exit_code == 0, result.output
    mock_run.assert_called_once()
    assert mock_run.call_args.args[1] == "question from stdin"


def test_ask_clear_removes_conversation(melos_repo: Path) -> None:
    path = ask_path(melos_repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}", encoding="utf-8")
    result = runner.invoke(app, ["ask", "--clear"])
    assert result.exit_code == 0, result.output
    assert "Conversation cleared." in result.output
    assert not path.is_file()


def test_ask_clear_rejects_question(melos_repo: Path) -> None:
    result = runner.invoke(app, ["ask", "--clear", "also a question"])
    assert result.exit_code == 1
    assert "not both" in result.output


def test_ask_work_rejects_active_task(melos_repo: Path) -> None:
    conversation = AskConversation()
    conversation.append_turns(
        user_text="fix the overflow",
        planner_text="Check the profile screen layout.",
        provider="cursor",
        model="composer-2.5",
        provider_session_id="sess-1",
    )
    conversation.save(melos_repo)
    Session.create(melos_repo, text="existing task", mode="prompt")

    result = runner.invoke(app, ["ask", "--work"])
    assert result.exit_code == 1
    assert "active task" in result.output.lower()


def test_ask_work_starts_task_from_conversation(melos_repo: Path) -> None:
    conversation = AskConversation()
    conversation.append_turns(
        user_text="fix the overflow on the profile screen",
        planner_text="Inspect the profile layout widgets.",
        provider="cursor",
        model="composer-2.5",
        provider_session_id="sess-1",
    )
    conversation.save(melos_repo)

    created: list[str] = []

    def fake_create(repo, text, **kwargs):
        created.append(text)
        return Session.create(repo, text="placeholder", mode="prompt")

    with patch("kcia.commands.ask._create_task", side_effect=fake_create) as mock_create:
        with patch("kcia.commands.ask._execute") as mock_execute:
            result = runner.invoke(app, ["ask", "--work"])

    assert result.exit_code == 0, result.output
    mock_create.assert_called_once()
    mock_execute.assert_called_once()
    assert "From a kcia ask conversation." in created[0]
    assert "fix the overflow on the profile screen" in created[0]


def test_ask_work_requires_conversation(melos_repo: Path) -> None:
    result = runner.invoke(app, ["ask", "--work"])
    assert result.exit_code == 1
    assert "No ask conversation" in result.output


def test_second_turn_uses_provider_resume(melos_repo: Path) -> None:
    captured: list[RunRequest] = []

    def runner_fn(adapter, req: RunRequest, on_event=None, should_cancel=None):
        captured.append(req)
        return RunResult(
            output_text=f"answer {len(captured)}",
            exit_code=0,
            session_id="sess-abc",
        )

    conversation = AskConversation.load(melos_repo)
    run_ask_turn(
        melos_repo,
        "first question",
        conversation,
        provider_runner=runner_fn,
    )
    conversation = AskConversation.load(melos_repo)
    run_ask_turn(
        melos_repo,
        "second question",
        conversation,
        provider_runner=runner_fn,
    )

    assert len(captured) == 2
    assert captured[0].resume is False
    assert captured[1].resume is True
    assert captured[1].session_id == "sess-abc"
    assert "## Prior turns" not in captured[1].prompt


def test_build_work_prompt_uses_transcript() -> None:
    conversation = AskConversation()
    conversation.append_turns(
        user_text="fix auth",
        planner_text="Look at lib/auth.dart",
        provider="cursor",
        model="composer-2.5",
        provider_session_id="sess-1",
    )
    text = build_work_prompt(conversation)
    assert "Latest user intent" in text
    assert "fix auth" in text
    assert "Look at lib/auth.dart" in text


def test_clear_conversation_helper(melos_repo: Path) -> None:
    conversation = AskConversation()
    conversation.append_turns(
        user_text="hi",
        planner_text="hello",
        provider="cursor",
        model="composer-2.5",
        provider_session_id="sess-1",
    )
    conversation.save(melos_repo)
    clear_conversation(melos_repo)
    assert not ask_path(melos_repo).is_file()
    assert AskConversation.load(melos_repo).turns == []
