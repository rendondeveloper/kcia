"""Generic subprocess runner for provider adapters."""

from __future__ import annotations

import inspect
import queue
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


def call_provider(
    runner: Callable[..., "RunResult"],
    adapter: ProviderAdapter,
    req: RunRequest,
    on_event: Callable[[StreamEvent], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> "RunResult":
    """Call a provider runner, forwarding only the callbacks it accepts.

    Runners injected by tests take just (adapter, req); passing the callbacks
    unconditionally would break them.
    """
    optional = {"on_event": on_event, "should_cancel": should_cancel}
    optional = {name: value for name, value in optional.items() if value is not None}
    if not optional:
        return runner(adapter, req)
    try:
        signature = inspect.signature(runner)
    except (TypeError, ValueError):
        return runner(adapter, req)
    accepted = {
        name: value for name, value in optional.items() if name in signature.parameters
    }
    return runner(adapter, req, **accepted)


def run_provider(
    adapter: ProviderAdapter,
    req: RunRequest,
    *,
    limits: RunnerLimits | None = None,
    on_event: Callable[[StreamEvent], None] | None = None,
    on_stuck_warning: Callable[[], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> RunResult:
    """Run a provider subprocess and consume normalized stream events.

    `should_cancel` is polled while reading: this loop is where a run spends its
    time, so it is the only place a Ctrl-C can be noticed and turned into a
    terminated child instead of a terminal that appears to hang.
    """
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

    # stdout is read in its own thread and handed over through a queue. Reading it
    # inline blocks on `readline()` until the provider says something, which is
    # exactly when the user wants out — a silent provider would swallow both the
    # cancel request and the idle timeout.
    stdout_lines: queue.Queue[str | None] = queue.Queue()

    def _stdout_reader() -> None:
        assert process.stdout is not None
        for line in iter(process.stdout.readline, ""):
            stdout_lines.put(line)
        stdout_lines.put(None)

    stdout_thread = threading.Thread(target=_stdout_reader, daemon=True)
    stdout_thread.start()

    assert process.stdin is not None
    process.stdin.write(req.prompt)
    process.stdin.close()

    timed_out = False
    cancelled = False
    interrupted = False
    assert process.stdout is not None

    def _stop() -> None:
        """Ask the child to stop, then insist."""
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()

    try:
        while True:
            if should_cancel is not None and should_cancel():
                cancelled = True
                interrupted = True
                _stop()
                break

            try:
                line = stdout_lines.get(timeout=0.1)
            except queue.Empty:
                line = ""
            if line is None:  # the reader reached EOF: the provider is done
                break
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
    finally:
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        stdout_thread.join(timeout=2)
        stderr_thread.join(timeout=2)
        # Drain whatever the reader had already queued before the child ended.
        while True:
            try:
                pending = stdout_lines.get_nowait()
            except queue.Empty:
                break
            if pending is None:
                continue
            parsed = adapter.parse_stream_line(pending, state)
            events.extend(parsed)
            if on_event:
                for event in parsed:
                    on_event(event)

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
        input_tokens=state.input_tokens,
        output_tokens=state.output_tokens,
        cached_tokens=state.cached_tokens,
        tool_calls=state.tool_calls,
        files_read=tuple(sorted(state.files_read)),
        files_written=tuple(sorted(state.files_written)),
        session_id=state.session_id,
        timed_out=timed_out,
        cancelled=cancelled,
        cancel_reason=_cancel_reason(interrupted, cancelled, timed_out),
    )


def _cancel_reason(interrupted: bool, cancelled: bool, timed_out: bool) -> str | None:
    if interrupted:
        return "cancelled by user"
    if cancelled:
        return "limits exceeded"
    if timed_out:
        return "idle timeout"
    return None


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
