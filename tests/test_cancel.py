"""Cancelling a run actually stops the provider subprocess."""

from __future__ import annotations

import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from kcia.cancel import Cancellation, interruptible
from kcia.providers.base import ProviderCapabilities, RunRequest
from kcia.providers.events import StreamState
from kcia.providers.runner import RunnerLimits, run_provider


@dataclass
class _FakeAdapter:
    """Runs a real child process, so termination is really exercised."""

    script: str
    capabilities: ProviderCapabilities = field(
        default_factory=lambda: ProviderCapabilities(
            supports_streaming=True,
            supports_sessions=False,
            supports_effort=False,
            supports_tool_restriction=False,
            supports_mcp_config=False,
        )
    )

    def build_command(self, req: RunRequest) -> list[str]:
        return [sys.executable, "-u", "-c", self.script]

    def parse_stream_line(self, line: str, state: StreamState) -> list[object]:
        state.final_text = (state.final_text or "") + line
        return []


SILENT_FOREVER = "import time\nwhile True: time.sleep(0.05)\n"
CHATTY_FOREVER = "import time\nwhile True:\n    print('tick')\n    time.sleep(0.05)\n"
QUICK = "print('done')\n"


def _request(tmp_path: Path) -> RunRequest:
    return RunRequest(
        prompt="",
        model="m",
        allow_edits=False,
        stream=True,
        workspace_dirs=[tmp_path],
        session_id=None,
        resume=False,
        effort=None,
        allowed_tools=None,
        disallowed_tools=None,
        cwd=tmp_path,
    )


@pytest.mark.parametrize("script", [SILENT_FOREVER, CHATTY_FOREVER], ids=["silent", "chatty"])
def test_cancel_stops_the_provider(tmp_path: Path, script: str) -> None:
    """A provider that says nothing must be as cancellable as one that streams.

    The silent case is the one that used to hang: the reader blocked on
    `readline()` and never looked at the cancel flag.
    """
    cancel = Cancellation()
    threading.Timer(0.3, cancel.request).start()

    started = time.monotonic()
    result = run_provider(_FakeAdapter(script), _request(tmp_path), should_cancel=cancel)
    elapsed = time.monotonic() - started

    assert result.cancelled is True
    assert result.cancel_reason == "cancelled by user"
    assert elapsed < 10  # not left waiting on the idle timeout


def test_a_normal_run_is_not_reported_as_cancelled(tmp_path: Path) -> None:
    cancel = Cancellation()
    result = run_provider(_FakeAdapter(QUICK), _request(tmp_path), should_cancel=cancel)
    assert result.cancelled is False
    assert result.cancel_reason is None
    assert "done" in result.output_text


def test_output_produced_before_the_stop_is_kept(tmp_path: Path) -> None:
    cancel = Cancellation()
    threading.Timer(0.5, cancel.request).start()
    result = run_provider(_FakeAdapter(CHATTY_FOREVER), _request(tmp_path), should_cancel=cancel)
    assert "tick" in result.output_text


def test_idle_timeout_fires_on_a_silent_provider(tmp_path: Path) -> None:
    result = run_provider(
        _FakeAdapter(SILENT_FOREVER),
        _request(tmp_path),
        limits=RunnerLimits(idle_timeout_seconds=1, stuck_warning_seconds=1),
    )
    assert result.timed_out is True
    assert result.cancel_reason == "idle timeout"


def test_interruptible_turns_sigint_into_a_request() -> None:
    import os
    import signal

    with interruptible() as cancel:
        assert cancel.requested is False
        os.kill(os.getpid(), signal.SIGINT)
        time.sleep(0.05)
        assert cancel.requested is True
        # The handler is restored on the first signal, so a second Ctrl-C is a
        # hard exit rather than another ignored request.
        assert signal.getsignal(signal.SIGINT) is not None


def test_a_cancelled_wave_goes_back_to_pending(tmp_path: Path) -> None:
    from kcia.providers.base import RunResult
    from kcia.waves.runner import WaveCancelled, run_wave
    from kcia.waves.session import Session

    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    session = Session.create(repo, text="add a loader", mode="prompt")

    def runner(adapter, req):  # noqa: ANN001 - injected test runner
        return RunResult(
            output_text="",
            stderr_text="",
            exit_code=0,
            cancelled=True,
            cancel_reason="cancelled by user",
        )

    with pytest.raises(WaveCancelled):
        run_wave("understanding", session, provider_runner=runner)

    assert session.wave_status("understanding") == "pending"
    assert "cancelled_at" in session.waves["understanding"]
