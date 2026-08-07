"""Generic subprocess runner for provider adapters."""

from __future__ import annotations

import subprocess
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass

from kcia.providers.base import ProviderAdapter, RunRequest, RunResult
from kcia.providers.events import ProviderError, StreamEvent, StreamState

DEFAULT_IDLE_TIMEOUT_SECONDS = 180
STUCK_WARNING_SECONDS = 90


@dataclass
class RunnerLimits:
    idle_timeout_seconds: int = DEFAULT_IDLE_TIMEOUT_SECONDS
    stuck_warning_seconds: int = STUCK_WARNING_SECONDS
    max_tool_calls: int | None = None
    max_files_read: int | None = None


def run_provider(
    adapter: ProviderAdapter,
    req: RunRequest,
    *,
    limits: RunnerLimits | None = None,
    on_event: Callable[[StreamEvent], None] | None = None,
    on_stuck_warning: Callable[[], None] | None = None,
) -> RunResult:
    """Run a provider subprocess and consume normalized stream events."""
    limits = limits or RunnerLimits()
    cmd = adapter.build_command(req)
    state = StreamState()
    events: list[StreamEvent] = []
    stderr_chunks: list[str] = []
    last_activity = time.monotonic()
    stuck_warned = False

    process = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=str(req.cwd),
    )

    def _stderr_reader() -> None:
        assert process.stderr is not None
        for chunk in iter(process.stderr.readline, ""):
            stderr_chunks.append(chunk)

    stderr_thread = threading.Thread(target=_stderr_reader, daemon=True)
    stderr_thread.start()

    assert process.stdin is not None
    process.stdin.write(req.prompt)
    process.stdin.close()

    timed_out = False
    cancelled = False
    assert process.stdout is not None
    try:
        while True:
            if process.poll() is not None:
                for line in process.stdout.readlines():
                    last_activity = time.monotonic()
                    parsed = adapter.parse_stream_line(line, state)
                    events.extend(parsed)
                    if on_event:
                        for event in parsed:
                            on_event(event)
                break

            line = process.stdout.readline()
            if line:
                last_activity = time.monotonic()
                parsed = adapter.parse_stream_line(line, state)
                events.extend(parsed)
                if on_event:
                    for event in parsed:
                        on_event(event)

                if limits.max_tool_calls and state.tool_calls > limits.max_tool_calls:
                    cancelled = True
                    process.kill()
                    break
                if limits.max_files_read and len(state.files_read) > limits.max_files_read:
                    cancelled = True
                    process.kill()
                    break
                continue

            idle_for = time.monotonic() - last_activity
            if idle_for >= limits.stuck_warning_seconds and not stuck_warned:
                stuck_warned = True
                if on_stuck_warning:
                    on_stuck_warning()

            if idle_for >= limits.idle_timeout_seconds:
                timed_out = True
                process.kill()
                break

            time.sleep(0.05)
    finally:
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        stderr_thread.join(timeout=2)

    output_text = state.final_text or "".join(
        event.text for event in events if hasattr(event, "text")
    )
    tokens_used = None
    if state.input_tokens or state.output_tokens:
        tokens_used = state.input_tokens + state.output_tokens

    return RunResult(
        output_text=output_text,
        stderr_text="".join(stderr_chunks),
        exit_code=process.returncode or 0,
        tokens_used=tokens_used,
        tool_calls=state.tool_calls,
        files_read=tuple(sorted(state.files_read)),
        files_written=tuple(sorted(state.files_written)),
        session_id=state.session_id,
        timed_out=timed_out,
        cancelled=cancelled,
        cancel_reason="limits exceeded" if cancelled else ("idle timeout" if timed_out else None),
    )


def iter_stream_events(
    adapter: ProviderAdapter,
    lines: Iterator[str],
    *,
    state: StreamState | None = None,
) -> Iterator[StreamEvent]:
    """Parse stream lines without running a subprocess (for tests)."""
    stream_state = state or StreamState()
    for line in lines:
        yield from adapter.parse_stream_line(line, stream_state)
