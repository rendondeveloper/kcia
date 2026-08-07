"""Cursor provider adapter."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from kcia.providers.base import AuthStatus, ProviderCapabilities, RunRequest
from kcia.providers.events import (
    ProviderError,
    StreamEvent,
    StreamState,
    TextDelta,
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

    def check_auth(self) -> AuthStatus:
        executable = self.locate()
        if executable is None:
            return AuthStatus.NOT_INSTALLED
        try:
            result = subprocess.run(
                [executable, "--version"],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired):
            return AuthStatus.UNKNOWN
        if result.returncode != 0:
            return AuthStatus.NOT_AUTHENTICATED
        return AuthStatus.AUTHENTICATED

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

        if event_type == "text_delta":
            text = payload.get("text", "")
            if text:
                events.append(TextDelta(text=text))

        elif event_type == "message":
            text = payload.get("content", "")
            if text:
                events.append(TextDelta(text=text))

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

        elif event_type == "error":
            message = str(payload.get("message", "unknown error"))
            events.append(ProviderError(message=message, fatal=True))

        return events

    def new_session_id(self) -> str | None:
        return None
