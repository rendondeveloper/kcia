"""Cursor provider adapter."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from kcia.providers.base import AuthStatus, ProviderCapabilities, RunRequest
from kcia.providers.events import (
    FileRead,
    FileWrite,
    ProviderError,
    StreamEvent,
    StreamState,
    TextDelta,
    ToolCallEnd,
    ToolCallStart,
    TurnEnd,
    UsageUpdate,
)
from kcia.providers.catalog import ProviderCatalogEntry


class CursorAdapter:
    id = "cursor"

    def __init__(self, catalog: ProviderCatalogEntry) -> None:
        self._catalog = catalog
        self.display_name = catalog.display_name
        self.executable = catalog.executable
        self.capabilities = ProviderCapabilities(
            supports_streaming=True,
            supports_sessions=True,
            supports_effort=False,
            supports_tool_restriction=False,
            supports_mcp_config=True,
        )

    def locate(self) -> str | None:
        return shutil.which(self.executable)

    def list_models(self) -> list[str]:
        return [model.id for model in self._catalog.models]

    def discover_models(self) -> list[str] | None:
        """Model ids the installed CLI actually offers, or None if unavailable.

        The catalog is curated by hand and drifts as Cursor renames models, so
        `kcia agent models --live` uses this to surface entries that no longer
        exist before they end up in someone's config.
        """
        executable = self.locate()
        if executable is None:
            return None
        try:
            result = subprocess.run(
                [executable, "--list-models"],
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if result.returncode != 0:
            return None
        return _parse_model_list(result.stdout)

    def check_auth(self) -> AuthStatus:
        executable = self.locate()
        if executable is None:
            return AuthStatus.NOT_INSTALLED
        # `cursor-agent status` is the real check; `--version` succeeds logged out.
        try:
            result = subprocess.run(
                [executable, "status"],
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired):
            return AuthStatus.UNKNOWN
        if result.returncode != 0:
            return AuthStatus.NOT_AUTHENTICATED
        return (
            AuthStatus.AUTHENTICATED
            if "logged in" in result.stdout.lower()
            else AuthStatus.NOT_AUTHENTICATED
        )

    def account(self) -> str | None:
        """Identity the provider reports, for `kcia doctor`."""
        executable = self.locate()
        if executable is None:
            return None
        try:
            result = subprocess.run(
                [executable, "status"],
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if result.returncode != 0:
            return None
        for line in result.stdout.splitlines():
            if "logged in as" in line.lower():
                return line.split("as", 1)[1].strip()
        return None

    def build_command(self, req: RunRequest) -> list[str]:
        executable = self.locate() or self.executable
        cmd = [
            executable,
            "--print",
            "--output-format",
            "stream-json" if req.stream else "json",
            "--model",
            req.model,
        ]
        if req.stream:
            cmd.append("--stream-partial-output")
        if req.allow_edits:
            cmd.append("--force")
        if req.mcp_config is not None:
            # Cursor reads .cursor/mcp.json itself; there is no per-run override,
            # so all kcia can do is stop the approval prompt from hanging --print.
            cmd.append("--approve-mcps")
        if req.resume and req.session_id:
            cmd.extend(["--resume", req.session_id])
        return cmd

    def parse_stream_line(self, line: str, state: StreamState) -> list[StreamEvent]:
        line = line.strip()
        if not line:
            return []
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            return []

        if not isinstance(payload, dict):
            return []

        events: list[StreamEvent] = []
        event_type = payload.get("type")

        if event_type == "system" and payload.get("subtype") == "init":
            session_id = payload.get("session_id")
            if isinstance(session_id, str):
                state.session_id = session_id

        elif event_type == "content_block_delta":
            delta = payload.get("delta") or {}
            if delta.get("type") == "text_delta":
                text = delta.get("text", "")
                if text:
                    events.append(TextDelta(text=text))

        elif event_type == "text_delta":
            text = payload.get("text", "")
            if text:
                events.append(TextDelta(text=text))

        elif event_type == "message":
            text = payload.get("content", "")
            if text:
                events.append(TextDelta(text=text))

        elif event_type == "assistant":
            message = payload.get("message") or {}
            for block in message.get("content", []):
                if block.get("type") == "text":
                    text = block.get("text", "")
                    if text:
                        events.append(TextDelta(text=text))

        elif event_type == "tool_use":
            state.tool_calls += 1
            name = str(payload.get("name", "unknown"))
            preview = json.dumps(payload.get("input", {}))[:200]
            events.append(ToolCallStart(name=name, input_preview=preview))

        elif event_type == "tool_result":
            name = str(payload.get("name", "unknown"))
            ok = not payload.get("is_error", False)
            events.append(ToolCallEnd(name=name, ok=ok))
            tool_input = payload.get("tool_input") or {}
            path = tool_input.get("file_path") or tool_input.get("path")
            if isinstance(path, str):
                if name in {"Read", "Glob", "Grep"}:
                    state.files_read.add(path)
                    events.append(FileRead(path=path))
                elif name in {"Edit", "Write", "NotebookEdit"}:
                    state.files_written.add(path)
                    events.append(FileWrite(path=path))

        elif event_type == "usage":
            input_tokens = int(payload.get("input_tokens", 0) or 0)
            output_tokens = int(payload.get("output_tokens", 0) or 0)
            cached = int(payload.get("cached_tokens", 0) or 0)
            state.input_tokens = input_tokens
            state.output_tokens = output_tokens
            state.cached_tokens = cached
            events.append(
                UsageUpdate(
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cached=cached,
                )
            )

        elif event_type in {"end", "done"}:
            final = payload.get("result") or payload.get("text")
            if isinstance(final, str):
                state.final_text = final
                events.append(TurnEnd(final_text=final))

        elif event_type == "result":
            usage = payload.get("usage") or {}
            input_tokens = int(usage.get("input_tokens", 0) or 0)
            output_tokens = int(usage.get("output_tokens", 0) or 0)
            cached = int(usage.get("cache_read_input_tokens", 0) or usage.get("cached_tokens", 0) or 0)
            state.input_tokens = input_tokens
            state.output_tokens = output_tokens
            state.cached_tokens = cached
            events.append(
                UsageUpdate(
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cached=cached,
                )
            )
            final = payload.get("result")
            if isinstance(final, str):
                state.final_text = final
                events.append(TurnEnd(final_text=final))

        elif event_type == "error":
            message = str(payload.get("message", "unknown error"))
            events.append(ProviderError(message=message, fatal=True))

        return events

    def new_session_id(self) -> str | None:
        return None


def _parse_model_list(stdout: str) -> list[str]:
    """Extract ids from `cursor-agent --list-models` output.

    Lines look like `composer-2.5 - Composer 2.5`; headers, blank lines and the
    trailing tip are ignored.
    """
    models: list[str] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line or " - " not in line:
            continue
        candidate = line.split(" - ", 1)[0].strip()
        if candidate and " " not in candidate:
            models.append(candidate)
    return models
