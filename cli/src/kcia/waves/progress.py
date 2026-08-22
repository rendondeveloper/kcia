"""Live progress reporting for wave runs.

Provider adapters already emit normalized stream events; this turns them into a
self-updating status display so a running wave shows who is working and on
what, instead of going silent until it finishes. Single-profile waves use one
status line; multi-profile waves reserve one line per profile under a shared
header.
"""

from __future__ import annotations

import itertools
import shutil
import sys
import threading
import time
from typing import Callable, TextIO

from kcia.providers.events import (
    FileRead,
    FileWrite,
    StreamEvent,
    TextDelta,
    ToolCallStart,
    UsageUpdate,
)
from kcia.usage import format_duration, format_tokens

_SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
_TICK = 0.1
_PERIODIC_TICK = 2.0


class WaveProgress:
    """Renders one status line per agent invocation, refreshed in place on a TTY.

    Used for waves and for the shorter calls around them — the ticket fetch, for
    one — so every provider call reports progress the same way.

    Off a TTY (CI, pipes, `| tee`) it degrades to plain one-shot lines, so logs
    stay readable and never fill with escape codes.
    """

    def __init__(
        self,
        wave_id: str,
        agent: str,
        provider: str,
        model: str,
        *,
        stream: TextIO | None = None,
        enabled: bool | None = None,
        clock: Callable[[], float] | None = None,
        periodic_updates: bool = False,
        periodic_tick: float = _PERIODIC_TICK,
    ) -> None:
        self._stream = stream if stream is not None else sys.stderr
        self._tty = enabled if enabled is not None else self._stream.isatty()
        self._header = f"{wave_id} · {agent} · {provider}/{model}"

        self._activity = "thinking"
        self._tool_calls = 0
        self._files_written: set[str] = set()
        self._tokens = 0
        self._lock = threading.Lock()
        self._spinner = itertools.cycle(_SPINNER_FRAMES)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._width = 0
        self._started_at: float | None = None
        self._clock = clock or time.monotonic
        self._periodic_updates = periodic_updates
        self._periodic_tick = periodic_tick

    def __enter__(self) -> WaveProgress:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.finish(failed=exc[0] is not None)

    def start(self) -> None:
        self._started_at = self._clock()
        if not self._tty:
            if self._periodic_updates:
                self._thread = threading.Thread(target=self._animate_periodic, daemon=True)
                self._thread.start()
            else:
                self._write_line(f"{self._header} — running")
            return
        self._thread = threading.Thread(target=self._animate, daemon=True)
        self._thread.start()

    def handle(self, event: StreamEvent) -> None:
        """Consume one provider event. Safe to call from the reader thread."""
        with self._lock:
            if isinstance(event, ToolCallStart):
                self._tool_calls += 1
                self._activity = _describe_tool(event)
            elif isinstance(event, FileWrite):
                self._files_written.add(event.path)
                self._activity = f"writing {_short_path(event.path)}"
            elif isinstance(event, FileRead):
                self._activity = f"reading {_short_path(event.path)}"
            elif isinstance(event, UsageUpdate):
                self._tokens = event.input_tokens + event.output_tokens
            elif isinstance(event, TextDelta):
                self._activity = "writing response"

    def note(self, text: str) -> None:
        """Replace the activity with a message of our own (e.g. `stopping…`).

        Called from a signal handler, so it must not do anything but set state.
        """
        with self._lock:
            self._activity = text

    def finish(self, *, failed: bool = False) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
        self._clear()

        elapsed = self.elapsed
        with self._lock:
            details = [format_duration(elapsed), _plural(self._tool_calls, "tool call")]
            if self._files_written:
                details.append(_plural(len(self._files_written), "file") + " written")
            if self._tokens:
                details.append(f"{format_tokens(self._tokens)} tokens")
        verb = "failed" if failed else "completed"
        self._write_line(f"{self._header} — {verb} ({', '.join(details)})")

    @property
    def elapsed(self) -> float:
        """Seconds since start(); 0 before the wave begins."""
        if self._started_at is None:
            return 0.0
        return self._clock() - self._started_at

    @property
    def activity(self) -> str:
        """What the agent is doing right now, as shown on the status line."""
        with self._lock:
            return self._activity

    def _animate(self) -> None:
        while not self._stop.is_set():
            self._animate_once()
            self._stop.wait(_TICK)

    def _animate_periodic(self) -> None:
        while not self._stop.is_set():
            self._write_line(self._format_status_line(next(self._spinner)))
            self._stop.wait(self._periodic_tick)

    def _animate_once(self) -> None:
        self._render(self._format_status_line(next(self._spinner)))

    def _format_status_line(self, spinner: str) -> str:
        elapsed = self.elapsed
        with self._lock:
            activity = self._activity
            tokens = self._tokens
            calls = self._tool_calls
        suffix = f"{format_duration(elapsed)} · {calls} tools"
        if tokens:
            suffix += f" · {format_tokens(tokens)} tok"
        return f"{spinner} {self._header} — {activity} · {suffix}"

    def _render(self, text: str) -> None:
        columns = shutil.get_terminal_size((80, 24)).columns
        line = text[: columns - 1]
        padding = " " * max(0, self._width - len(line))
        self._stream.write(f"\r{line}{padding}")
        self._stream.flush()
        self._width = len(line)

    def _clear(self) -> None:
        if self._tty and self._width:
            self._stream.write("\r" + " " * self._width + "\r")
            self._stream.flush()
            self._width = 0

    def _write_line(self, text: str) -> None:
        self._stream.write(text + "\n")
        self._stream.flush()


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _describe_tool(event: ToolCallStart) -> str:
    preview = (event.input_preview or "").strip().replace("\n", " ")
    if not preview:
        return f"{event.name}"
    if len(preview) > 48:
        preview = preview[:47] + "…"
    return f"{event.name}: {preview}"


def _short_path(path: str) -> str:
    parts = path.replace("\\", "/").split("/")
    return "/".join(parts[-2:]) if len(parts) > 2 else path


class _LineState:
    """Mutable per-profile counters for `MultiWaveProgress`."""

    def __init__(self) -> None:
        self.activity = "waiting"
        self.tool_calls = 0
        self.files_written: set[str] = set()
        self.tokens = 0

    def apply(self, event: StreamEvent) -> None:
        if isinstance(event, ToolCallStart):
            self.tool_calls += 1
            self.activity = _describe_tool(event)
        elif isinstance(event, FileWrite):
            self.files_written.add(event.path)
            self.activity = f"writing {_short_path(event.path)}"
        elif isinstance(event, FileRead):
            self.activity = f"reading {_short_path(event.path)}"
        elif isinstance(event, UsageUpdate):
            self.tokens = event.input_tokens + event.output_tokens
        elif isinstance(event, TextDelta):
            self.activity = "writing response"


class MultiWaveProgress:
    """One live status line per profile under a shared wave header.

    Used for `implementation` and `documentation-final` when a plan `execution:`
    block lists more than one profile. Single-profile waves keep using
    `WaveProgress`.
    """

    def __init__(
        self,
        wave_id: str,
        agent: str,
        provider: str,
        model: str,
        profile_ids: list[str],
        *,
        stream: TextIO | None = None,
        enabled: bool | None = None,
        clock: Callable[[], float] | None = None,
        periodic_updates: bool = False,
        periodic_tick: float = _PERIODIC_TICK,
    ) -> None:
        self._stream = stream if stream is not None else sys.stderr
        self._tty = enabled if enabled is not None else self._stream.isatty()
        self._header = f"{wave_id} · {agent} · {provider}/{model}"
        self._profile_ids = list(profile_ids)
        self._label_width = max((len(pid) for pid in self._profile_ids), default=0)
        self._slots = {profile_id: _LineState() for profile_id in self._profile_ids}
        self._lock = threading.Lock()
        self._spinner = itertools.cycle(_SPINNER_FRAMES)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._drawn = False
        self._line_widths: list[int] = [0] * len(self._profile_ids)
        self._started_at: float | None = None
        self._clock = clock or time.monotonic
        self._periodic_updates = periodic_updates
        self._periodic_tick = periodic_tick

    def __enter__(self) -> MultiWaveProgress:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.finish(failed=exc[0] is not None)

    def start(self) -> None:
        self._started_at = self._clock()
        if not self._tty:
            if self._periodic_updates:
                self._thread = threading.Thread(target=self._animate_periodic, daemon=True)
                self._thread.start()
            else:
                self._write_line(f"{self._header} — running")
            return
        self._thread = threading.Thread(target=self._animate, daemon=True)
        self._thread.start()

    def handle(self, event: StreamEvent, profile_id: str | None = None) -> None:
        """Consume one provider event for a specific profile."""
        pid = profile_id or getattr(event, "profile_id", None)
        if pid is None or pid not in self._slots:
            return
        with self._lock:
            self._slots[pid].apply(event)

    def note(self, text: str) -> None:
        """Replace every profile's activity (e.g. `stopping…`)."""
        with self._lock:
            for slot in self._slots.values():
                slot.activity = text

    def finish(self, *, failed: bool = False) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None

        summaries = [
            self._format_summary(profile_id, failed) for profile_id in self._profile_ids
        ]
        if self._tty and self._drawn:
            n = len(self._profile_ids)
            self._stream.write(f"\033[{n}A")
            for line in summaries:
                self._stream.write(f"\r\033[2K{line}\n")
            self._stream.flush()
            self._drawn = False
            return
        for line in summaries:
            self._write_line(line)

    def activity_for(self, profile_id: str) -> str:
        """What that profile is doing right now, as shown on its status line."""
        with self._lock:
            return self._slots[profile_id].activity

    @property
    def elapsed(self) -> float:
        """Seconds since start(); 0 before the wave begins."""
        if self._started_at is None:
            return 0.0
        return self._clock() - self._started_at

    def _animate(self) -> None:
        while not self._stop.is_set():
            self._animate_once()
            self._stop.wait(_TICK)

    def _animate_periodic(self) -> None:
        while not self._stop.is_set():
            spinner = next(self._spinner)
            for profile_id in self._profile_ids:
                self._write_line(self._format_profile_line(profile_id, spinner, off_tty=True))
            self._stop.wait(self._periodic_tick)

    def _animate_once(self) -> None:
        spinner = next(self._spinner)
        lines = [
            self._format_profile_line(profile_id, spinner, off_tty=False)
            for profile_id in self._profile_ids
        ]
        columns = max(2, shutil.get_terminal_size((80, 24)).columns)
        n = len(lines)
        if self._drawn:
            self._stream.write(f"\033[{n}A")
        else:
            self._stream.write(self._header[: columns - 1] + "\n")
            self._drawn = True
            self._line_widths = [0] * n
        new_widths: list[int] = []
        for i, text in enumerate(lines):
            line = text[: columns - 1]
            padding = " " * max(0, self._line_widths[i] - len(line))
            self._stream.write(f"\r{line}{padding}\n")
            new_widths.append(len(line))
        self._stream.flush()
        self._line_widths = new_widths

    def _format_profile_line(self, profile_id: str, spinner: str, *, off_tty: bool) -> str:
        elapsed = self.elapsed
        with self._lock:
            slot = self._slots[profile_id]
            activity = slot.activity
            calls = slot.tool_calls
            tokens = slot.tokens
        suffix = f"{format_duration(elapsed)} · {calls} tools"
        if tokens:
            suffix += f" · {format_tokens(tokens)} tok"
        if off_tty:
            return f"{spinner} {profile_id} — {activity} · {suffix}"
        label = profile_id.ljust(self._label_width)
        return f"  {spinner} {label} {activity} · {suffix}"

    def _format_summary(self, profile_id: str, failed: bool) -> str:
        elapsed = self.elapsed
        with self._lock:
            slot = self._slots[profile_id]
            details = [format_duration(elapsed), _plural(slot.tool_calls, "tool call")]
            if slot.files_written:
                details.append(_plural(len(slot.files_written), "file") + " written")
            if slot.tokens:
                details.append(f"{format_tokens(slot.tokens)} tokens")
        verb = "failed" if failed else "completed"
        if self._tty:
            return f"  {profile_id} — {verb} ({', '.join(details)})"
        return f"{self._header} · {profile_id} — {verb} ({', '.join(details)})"

    def _write_line(self, text: str) -> None:
        self._stream.write(text + "\n")
        self._stream.flush()


class StepProgress:
    """Spinner for a named CLI step (git operations in `kcia done`, not waves).

    On a TTY it reuses the same in-place spinner as `WaveProgress`. Off a TTY,
    when `periodic_updates` is on (the default), it reprints a status line every
    few seconds so a long git step does not look stuck. The caller still prints
    the step label itself. Pass `periodic_updates=False` to stay silent off a
    TTY.
    """

    def __init__(
        self,
        label: str,
        *,
        stream: TextIO | None = None,
        enabled: bool | None = None,
        clock: Callable[[], float] | None = None,
        periodic_updates: bool = True,
        periodic_tick: float = _PERIODIC_TICK,
    ) -> None:
        self._label = label
        self._stream = stream if stream is not None else sys.stderr
        self._tty = enabled if enabled is not None else self._stream.isatty()
        self._lock = threading.Lock()
        self._spinner = itertools.cycle(_SPINNER_FRAMES)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._width = 0
        self._started_at: float | None = None
        self._clock = clock or time.monotonic
        self._periodic_updates = periodic_updates
        self._periodic_tick = periodic_tick

    def __enter__(self) -> StepProgress:
        self.start()
        return self

    def __exit__(self, exc_type: object, *exc: object) -> None:
        self.finish(failed=exc[0] is not None)

    def start(self) -> None:
        self._started_at = self._clock()
        if not self._tty:
            if self._periodic_updates:
                self._thread = threading.Thread(
                    target=self._animate_periodic, daemon=True
                )
                self._thread.start()
            return
        self._thread = threading.Thread(target=self._animate, daemon=True)
        self._thread.start()

    def finish(self, *, failed: bool = False) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
        self._clear()

    @property
    def elapsed(self) -> float:
        """Seconds since start(); 0 before the step begins."""
        if self._started_at is None:
            return 0.0
        return self._clock() - self._started_at

    def _animate(self) -> None:
        while not self._stop.is_set():
            self._render(f"{next(self._spinner)} {self._label}")
            self._stop.wait(_TICK)

    def _animate_periodic(self) -> None:
        while not self._stop.wait(self._periodic_tick):
            self._write_line(
                f"{next(self._spinner)} {self._label} · {format_duration(self.elapsed)}"
            )

    def _render(self, text: str) -> None:
        columns = shutil.get_terminal_size((80, 24)).columns
        line = text[: columns - 1]
        padding = " " * max(0, self._width - len(line))
        self._stream.write(f"\r{line}{padding}")
        self._stream.flush()
        self._width = len(line)

    def _clear(self) -> None:
        if self._tty and self._width:
            self._stream.write("\r" + " " * self._width + "\r")
            self._stream.flush()
            self._width = 0

    def _write_line(self, text: str) -> None:
        self._stream.write(text + "\n")
        self._stream.flush()
