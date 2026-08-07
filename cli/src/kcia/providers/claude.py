"""Claude Code provider adapter."""

from __future__ import annotations

import json
import shutil
import subprocess
import uuid
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

_EDIT_TOOLS = ("Edit", "Write", "NotebookEdit")


class ClaudeAdapter:
    id = "claude"

    def __init__(self, catalog: ProviderCatalogEntry) -> None:
        self._catalog = catalog
        self.display_name = catalog.display_name
        self.executable = catalog.executable
        self.capabilities = ProviderCapabilities(
            supports_streaming=True,
            supports_sessions=True,
            supports_effort=True,
            supports_tool_restriction=True,
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

        if req.allow_edits and req.allowed_tools:
            cmd.extend(["--permission-mode", "acceptEdits"])
            cmd.extend(["--allowed-tools", *req.allowed_tools])
        elif req.allow_edits:
            cmd.extend(["--permission-mode", "bypassPermissions"])
        else:
            cmd.extend(["--permission-mode", "default"])
            disallowed = list(_EDIT_TOOLS)
            if req.disallowed_tools:
                disallowed.extend(req.disallowed_tools)
            cmd.extend(["--disallowed-tools", *disallowed])

        if req.stream:
            cmd.extend(["--verbose", "--include-partial-messages"])

        if req.resume and req.session_id:
            cmd.extend(["--resume", req.session_id])
        elif req.session_id:
            cmd.extend(["--session-id", req.session_id])

        for workspace in req.workspace_dirs:
            cmd.extend(["--add-dir", str(workspace)])

        if req.effort:
            effort = "max" if req.effort == "high" else req.effort
            cmd.extend(["--effort", effort])

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

        elif event_type == "result":
            usage = payload.get("usage") or {}
            input_tokens = int(usage.get("input_tokens", 0) or 0)
            output_tokens = int(usage.get("output_tokens", 0) or 0)
            cached = int(usage.get("cache_read_input_tokens", 0) or 0)
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
            message = str(payload.get("error", {}).get("message", payload.get("message", "unknown error")))
            events.append(ProviderError(message=message, fatal=True))

        return events

    def new_session_id(self) -> str | None:
        return str(uuid.uuid4())
