"""Normalized provider stream events."""

from __future__ import annotations

from dataclasses import dataclass, field


class StreamEvent:
    """Base class for normalized provider events."""


@dataclass
class StreamState:
    """Mutable parser state shared across stream lines."""

    session_id: str | None = None
    tool_calls: int = 0
    files_read: set[str] = field(default_factory=set)
    files_written: set[str] = field(default_factory=set)
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    final_text: str | None = None


@dataclass
class TextDelta(StreamEvent):
    text: str


@dataclass
class ToolCallStart(StreamEvent):
    name: str
    input_preview: str


@dataclass
class ToolCallEnd(StreamEvent):
    name: str
    ok: bool


@dataclass
class FileRead(StreamEvent):
    path: str


@dataclass
class FileWrite(StreamEvent):
    path: str


@dataclass
class UsageUpdate(StreamEvent):
    input_tokens: int
    output_tokens: int
    cached: int


@dataclass
class TurnEnd(StreamEvent):
    final_text: str | None


@dataclass
class ProviderError(StreamEvent):
    message: str
    fatal: bool
