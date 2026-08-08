"""Provider adapter protocol and request/response types."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Protocol

from kcia.providers.events import StreamEvent, StreamState


class AuthStatus(Enum):
    UNKNOWN = "unknown"
    AUTHENTICATED = "authenticated"
    NOT_AUTHENTICATED = "not_authenticated"
    NOT_INSTALLED = "not_installed"


@dataclass(frozen=True)
class ProviderCapabilities:
    supports_streaming: bool
    supports_sessions: bool
    supports_effort: bool
    supports_tool_restriction: bool
    supports_mcp_config: bool


@dataclass(frozen=True)
class RunRequest:
    prompt: str
    model: str
    allow_edits: bool
    stream: bool
    workspace_dirs: list[Path]
    session_id: str | None
    resume: bool
    effort: str | None
    allowed_tools: list[str] | None
    disallowed_tools: list[str] | None
    cwd: Path
    # Provider-specific MCP config file, when the wave's role has servers.
    mcp_config: Path | None = None
    mcp_tools: list[str] | None = None


@dataclass
class RunResult:
    output_text: str = ""
    stderr_text: str = ""
    exit_code: int = 0
    tokens_used: int | None = None
    # Kept separate as well: input, output, and cached tokens are not interchangeable.
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    tool_calls: int = 0
    files_read: tuple[str, ...] = field(default_factory=tuple)
    files_written: tuple[str, ...] = field(default_factory=tuple)
    session_id: str | None = None
    timed_out: bool = False
    cancelled: bool = False
    cancel_reason: str | None = None


class ProviderAdapter(Protocol):
    id: str
    display_name: str
    executable: str
    capabilities: ProviderCapabilities

    def locate(self) -> str | None:
        """Return absolute path to the executable, or None if not found."""

    def list_models(self) -> list[str]:
        """Return model ids available for this provider."""

    def check_auth(self) -> AuthStatus:
        """Verify subscription login status."""

    def build_command(self, req: RunRequest) -> list[str]:
        """Build argv for a provider subprocess."""

    def parse_stream_line(self, line: str, state: StreamState) -> list[StreamEvent]:
        """Translate one stdout line into normalized stream events."""

    def new_session_id(self) -> str | None:
        """Return a new session id when supported."""
