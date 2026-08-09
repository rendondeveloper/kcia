"""Live wave progress reporting."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from kcia.providers.events import FileRead, FileWrite, TextDelta, ToolCallStart, UsageUpdate
from kcia.waves.progress import WaveProgress


@pytest.fixture()
def git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    return repo


class _FakeTty(io.StringIO):
    def isatty(self) -> bool:
        return True


def _progress(stream: io.StringIO, *, enabled: bool | None = None) -> WaveProgress:
    return WaveProgress(
        "implementation",
        "builder",
        "cursor",
        "claude-sonnet-5",
        stream=stream,
        enabled=enabled,
    )


def test_non_tty_emits_plain_lines_without_escapes() -> None:
    buf = io.StringIO()
    progress = _progress(buf, enabled=False)
    progress.start()
    progress.handle(ToolCallStart(name="Read", input_preview="lib/main.dart"))
    progress.finish()

    output = buf.getvalue()
    assert "\r" not in output
    lines = output.strip().splitlines()
    assert lines[0] == "implementation · builder · cursor/claude-sonnet-5 — running"
    assert "completed" in lines[1]


def test_summary_names_wave_agent_and_model() -> None:
    buf = io.StringIO()
    progress = _progress(buf, enabled=False)
    progress.start()
    progress.handle(ToolCallStart(name="Read", input_preview="a.dart"))
    progress.handle(ToolCallStart(name="Edit", input_preview="b.dart"))
    progress.handle(FileWrite(path="/repo/lib/b.dart"))
    progress.handle(UsageUpdate(input_tokens=4000, output_tokens=9000, cached=0))
    progress.finish()

    summary = buf.getvalue().strip().splitlines()[-1]
    assert "implementation · builder · cursor/claude-sonnet-5" in summary
    assert "2 tool calls" in summary
    assert "1 file written" in summary
    assert "13k tokens" in summary


def test_failed_wave_is_reported_as_failed() -> None:
    buf = io.StringIO()
    progress = _progress(buf, enabled=False)
    progress.start()
    progress.finish(failed=True)
    assert "failed" in buf.getvalue()


def test_tty_render_stays_within_terminal_width() -> None:
    buf = _FakeTty()
    progress = _progress(buf)
    progress.handle(ToolCallStart(name="Read", input_preview="x" * 500))
    # Drive one frame directly rather than racing the animation thread.
    progress._animate_once()
    frame = buf.getvalue().lstrip("\r").rstrip()
    assert len(frame) < 200
    assert "\n" not in frame


def test_events_update_the_reported_activity() -> None:
    buf = io.StringIO()
    progress = _progress(buf, enabled=False)

    progress.handle(FileRead(path="/repo/lib/a.dart"))
    assert progress.activity == "reading lib/a.dart"

    progress.handle(FileWrite(path="/repo/lib/b.dart"))
    assert progress.activity == "writing lib/b.dart"

    progress.handle(TextDelta(text="hello"))
    assert progress.activity == "writing response"


def test_context_manager_finishes_the_line() -> None:
    buf = io.StringIO()
    with _progress(buf, enabled=False):
        pass
    assert "completed" in buf.getvalue()


def test_run_wave_forwards_provider_events_and_announces_the_agent(git_repo) -> None:
    """The whole point: events must reach the reporter through run_wave."""
    from kcia.providers.base import RunResult
    from kcia.waves.runner import run_wave
    from kcia.waves.session import Session

    Session.create(git_repo, text="fix bug", mode="prompt")
    session = Session.load(git_repo)

    def fake_runner(adapter, req, *, on_event=None):
        assert on_event is not None, "run_wave must forward on_event"
        on_event(ToolCallStart(name="Read", input_preview="lib/main.dart"))
        on_event(FileWrite(path="/repo/lib/main.dart"))
        return RunResult(output_text="# Understanding\n\ndone", exit_code=0)

    seen: list = []
    started: list = []
    result = run_wave(
        "understanding",
        session,
        provider_runner=fake_runner,
        on_event=seen.append,
        on_wave_start=lambda wave, agent: started.append((wave.id, agent.provider, agent.model)),
    )

    assert result.status == "completed"
    assert [type(event).__name__ for event in seen] == ["ToolCallStart", "FileWrite"]
    assert started and started[0][0] == "understanding"


def test_run_wave_still_accepts_runners_without_on_event(git_repo) -> None:
    """Older/simple runners take (adapter, req) only and must keep working."""
    from kcia.providers.base import RunResult
    from kcia.waves.runner import run_wave
    from kcia.waves.session import Session

    Session.create(git_repo, text="fix bug", mode="prompt")
    session = Session.load(git_repo)

    result = run_wave(
        "understanding",
        session,
        provider_runner=lambda *_a, **_k: RunResult(output_text="ok", exit_code=0),
        on_event=lambda event: None,
    )
    assert result.status == "completed"


def test_summary_leads_with_elapsed_time() -> None:
    clock = {"now": 0.0}
    buf = io.StringIO()
    progress = WaveProgress(
        "implementation",
        "builder",
        "cursor",
        "claude-sonnet-5",
        stream=buf,
        enabled=False,
        clock=lambda: clock["now"],
    )
    progress.start()
    clock["now"] = 612.0
    progress.finish()

    summary = buf.getvalue().strip().splitlines()[-1]
    assert "completed (10m12s," in summary


def test_elapsed_is_zero_before_the_wave_starts() -> None:
    progress = _progress(io.StringIO(), enabled=False)
    assert progress.elapsed == 0.0
