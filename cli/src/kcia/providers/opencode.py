"""OpenCode provider adapter."""

from __future__ import annotations

import json
import re
import shutil
import subprocess

from kcia.providers.base import AuthStatus, ProviderCapabilities, RunRequest
from kcia.providers.catalog import ProviderCatalogEntry
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

_READ_TOOLS = {"read", "grep", "glob", "list"}
_WRITE_TOOLS = {"write", "edit"}
_MODEL_ID = re.compile(r"^[\w.-]+/[\w.-]+$")
_CREDENTIAL_COUNT = re.compile(r"(\d+)\s+credentials", re.IGNORECASE)


class OpenCodeAdapter:
    id = "opencode"

    def __init__(self, catalog: ProviderCatalogEntry) -> None:
        self._catalog = catalog
        self.display_name = catalog.display_name
        self.executable = catalog.executable
        self.capabilities = ProviderCapabilities(
            supports_streaming=True,
            supports_sessions=True,
            supports_effort=True,
            supports_tool_restriction=False,
            supports_mcp_config=False,
        )

    def locate(self) -> str | None:
        return shutil.which(self.executable)

    def list_models(self) -> list[str]:
        live = self.discover_models()
        if live:
            return live
        return [model.id for model in self._catalog.models]

    def discover_models(self) -> list[str] | None:
        """Model ids the installed CLI actually offers, or None if unavailable."""
        executable = self.locate()
        if executable is None:
            return None
        try:
            result = subprocess.run(
                [executable, "models", "--verbose"],
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if result.returncode != 0:
            return None
        parsed = _parse_model_list(result.stdout)
        return parsed or None

    def check_auth(self) -> AuthStatus:
        executable = self.locate()
        if executable is None:
            return AuthStatus.NOT_INSTALLED
        try:
            result = subprocess.run(
                [executable, "auth", "list"],
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired):
            return AuthStatus.UNKNOWN
        return _parse_auth_list(result.stdout, result.stderr, result.returncode)

    def account(self) -> str | None:
        """Identity the provider reports, for `kcia doctor`."""
        executable = self.locate()
        if executable is None:
            return None
        try:
            result = subprocess.run(
                [executable, "auth", "list"],
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if result.returncode != 0:
            return None
        names = _auth_provider_names(result.stdout)
        return ", ".join(names) if names else None

    def build_command(self, req: RunRequest) -> list[str]:
        executable = self.locate() or self.executable
        cmd = [executable, "run", "--format", "json", "-m", req.model]
        if req.effort:
            cmd.extend(["--variant", req.effort])
        if req.resume and req.session_id:
            cmd.extend(["-s", req.session_id])
        elif req.resume:
            cmd.append("-c")
        elif req.session_id:
            cmd.extend(["-s", req.session_id])
        cmd.extend(["--dir", str(req.cwd)])
        if req.allow_edits:
            cmd.append("--auto")
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

        session_id = payload.get("sessionID") or payload.get("session_id")
        if isinstance(session_id, str) and session_id:
            state.session_id = session_id

        event_type = str(payload.get("type") or "")
        part = payload.get("part") if isinstance(payload.get("part"), dict) else {}

        if event_type in {"step_start", "step_start"}:
            return []

        if event_type == "text":
            text = part.get("text") or payload.get("text") or ""
            if not text:
                return []
            state.final_text = (state.final_text or "") + text
            return [TextDelta(text=text)]

        if event_type in {"tool_use", "tool_use"}:
            return _tool_events(part, payload, state)

        if event_type in {"step_finish", "step_finish"}:
            return _step_finish_events(part, payload, state)

        if event_type == "error":
            return [ProviderError(message=_error_message(payload), fatal=True)]

        return []

    def new_session_id(self) -> str | None:
        return None


def _parse_model_list(stdout: str) -> list[str]:
    """Extract `provider/model` ids from `opencode models` / `--verbose` output."""
    models: list[str] = []
    for line in stdout.splitlines():
        candidate = line.strip()
        if _MODEL_ID.match(candidate) and candidate not in models:
            models.append(candidate)
    return models


def _parse_auth_list(stdout: str, stderr: str, returncode: int) -> AuthStatus:
    """Interpret `opencode auth list` output.

    The CLI prints a TUI table, not JSON. "0 credentials" is logged out;
    a positive count or a parseable provider list is logged in. Unparseable
    success output is UNKNOWN rather than a false negative.
    """
    text = "\n".join(part for part in (stdout, stderr) if part)
    count = _credential_count(text)
    if count == 0:
        return AuthStatus.NOT_AUTHENTICATED
    if count is not None and count > 0:
        return AuthStatus.AUTHENTICATED

    parsed = _auth_providers_from_json(stdout)
    if parsed is None:
        parsed = _auth_providers_from_json(stderr)
    if parsed is not None:
        return AuthStatus.AUTHENTICATED if parsed else AuthStatus.NOT_AUTHENTICATED

    names = _auth_provider_names(text)
    if names:
        return AuthStatus.AUTHENTICATED
    if returncode != 0:
        return AuthStatus.NOT_AUTHENTICATED
    return AuthStatus.UNKNOWN


def _credential_count(text: str) -> int | None:
    match = _CREDENTIAL_COUNT.search(text)
    return int(match.group(1)) if match else None


def _auth_providers_from_json(text: str) -> list[str] | None:
    text = text.strip()
    if not text:
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    if isinstance(payload, list):
        return [str(item) for item in payload if item]
    if not isinstance(payload, dict):
        return None
    raw = payload.get("providers")
    if raw is None:
        raw = payload.get("credentials")
    if isinstance(raw, list):
        return [str(item) for item in raw if item]
    if isinstance(raw, dict):
        return [str(key) for key in raw if key]
    skip = {"path", "file", "count", "credentials"}
    return [str(key) for key in payload if key not in skip]


def _auth_provider_names(text: str) -> list[str]:
    names: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or "credential" in stripped.lower():
            continue
        if stripped.startswith(("┌", "│", "└", "╭", "╮", "╰", "╯", "─")):
            continue
        cleaned = re.sub(r"^[●•*\-]\s*", "", stripped).strip()
        if not cleaned or " " in cleaned:
            continue
        if cleaned.lower() in {"credentials", "providers"}:
            continue
        names.append(cleaned)
    return names


def _tool_events(part: dict, payload: dict, state: StreamState) -> list[StreamEvent]:
    name = str(part.get("tool") or payload.get("tool") or "unknown")
    tool_state = part.get("state") if isinstance(part.get("state"), dict) else {}
    raw_input = tool_state.get("input") or part.get("input") or payload.get("input") or {}
    if not isinstance(raw_input, dict):
        raw_input = {}
    preview = json.dumps(raw_input)[:200]
    status = str(tool_state.get("status") or payload.get("status") or "completed")
    events: list[StreamEvent] = []
    state.tool_calls += 1
    events.append(ToolCallStart(name=name, input_preview=preview))
    if status in {"pending", "running", "started"}:
        return events

    ok = status not in {"error", "failed", "cancelled"}
    events.append(ToolCallEnd(name=name, ok=ok))
    path = _tool_path(raw_input)
    if path:
        lowered = name.lower()
        if lowered in _READ_TOOLS:
            state.files_read.add(path)
            events.append(FileRead(path=path))
        elif lowered in _WRITE_TOOLS:
            state.files_written.add(path)
            events.append(FileWrite(path=path))
    return events


def _tool_path(raw_input: dict) -> str | None:
    for key in ("filePath", "path", "file_path"):
        value = raw_input.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _step_finish_events(
    part: dict, payload: dict, state: StreamState
) -> list[StreamEvent]:
    tokens = part.get("tokens") if isinstance(part.get("tokens"), dict) else {}
    if not tokens:
        tokens = payload.get("tokens") if isinstance(payload.get("tokens"), dict) else {}
    input_tokens = int(tokens.get("input") or 0)
    output_tokens = int(tokens.get("output") or 0)
    cache = tokens.get("cache")
    if isinstance(cache, dict):
        cached = int(cache.get("read") or 0)
    else:
        cached = int(tokens.get("cache_read") or 0)
    state.input_tokens = input_tokens
    state.output_tokens = output_tokens
    state.cached_tokens = cached
    events: list[StreamEvent] = [
        UsageUpdate(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached=cached,
        )
    ]
    reason = part.get("reason") or payload.get("reason")
    if reason in {"stop", "end", "done"}:
        events.append(TurnEnd(final_text=state.final_text))
    return events


def _error_message(payload: dict) -> str:
    error = payload.get("error")
    if isinstance(error, dict):
        data = error.get("data") if isinstance(error.get("data"), dict) else {}
        message = data.get("message") or error.get("message")
        if message:
            return str(message)
    if payload.get("message"):
        return str(payload["message"])
    return "unknown error"
