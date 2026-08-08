"""Ctrl-C that actually stops what is running.

A wave spends nearly all of its time inside a provider subprocess, so a handler
that only sets a flag and is read between waves leaves the terminal looking
frozen: the user presses Ctrl-C, nothing happens, and the only way out is to
close the terminal. The flag has to be *polled by the loop reading the provider*
and turned into a real `terminate()`.

Second Ctrl-C is a hard exit: after the first one the default handler is
restored, so a provider that refuses to die never traps the user.
"""

from __future__ import annotations

import signal
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Callable, Iterator


@dataclass
class Cancellation:
    """Shared cancel flag, polled by whatever is running."""

    _requested: bool = field(default=False, repr=False)
    on_request: Callable[[], None] | None = None

    @property
    def requested(self) -> bool:
        return self._requested

    def request(self) -> None:
        self._requested = True
        if self.on_request is not None:
            self.on_request()

    def __call__(self) -> bool:
        """Usable directly as the `should_cancel` callback."""
        return self._requested


@contextmanager
def interruptible(on_request: Callable[[], None] | None = None) -> Iterator[Cancellation]:
    """Turn SIGINT into a cancel request for the duration of the block."""
    cancellation = Cancellation(on_request=on_request)
    previous = signal.getsignal(signal.SIGINT)

    def _handle(signum: int, frame: object) -> None:
        # Restore first: a second Ctrl-C must raise KeyboardInterrupt and exit,
        # even if the provider ignores its own signal.
        signal.signal(signal.SIGINT, previous)
        cancellation.request()

    try:
        signal.signal(signal.SIGINT, _handle)
    except ValueError:
        # Not the main thread (tests, embedded use): cancellation still works
        # through the flag, it just cannot be driven by a signal.
        yield cancellation
        return

    try:
        yield cancellation
    finally:
        signal.signal(signal.SIGINT, previous)
