"""OpenCode adapter: command shape, stream parsing, auth, and model listing."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

from kcia.providers.base import AuthStatus, RunRequest
from kcia.providers.catalog import load_catalog
from kcia.providers.events import (
    FileRead,
    FileWrite,
    ProviderError,
    StreamState,
    TextDelta,
    ToolCallEnd,
    ToolCallStart,
    TurnEnd,
    UsageUpdate,
)
from kcia.providers.opencode import (
    OpenCodeAdapter,
    _parse_auth_list,
    _parse_model_list,
)

VERBOSE_MODELS = """\
opencode/big-pickle
{
  "id": "big-pickle",
  "name": "Big Pickle"
}
opencode/hy3-free
{
  "id": "hy3-free"
}
"""


def _completed(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


def _adapter() -> OpenCodeAdapter:
    return OpenCodeAdapter(load_catalog()["opencode"])


def _request(**overrides: object) -> RunRequest:
    values: dict = {
        "prompt": "p",
        "model": "opencode/big-pickle",
        "allow_edits": False,
        "stream": True,
        "workspace_dirs": [Path("/tmp")],
        "session_id": None,
        "resume": False,
        "effort": None,
        "allowed_tools": None,
        "disallowed_tools": None,
        "cwd": Path("/tmp"),
        "mcp_config": None,
    }
    values.update(overrides)
    return RunRequest(**values)


def test_capabilities_and_session_id() -> None:
    adapter = _adapter()
    assert adapter.id == "opencode"
    assert adapter.executable == "opencode"
    assert adapter.display_name == "OpenCode"
    assert adapter.capabilities.supports_streaming
    assert adapter.capabilities.supports_sessions
    assert adapter.capabilities.supports_effort
    assert not adapter.capabilities.supports_tool_restriction
    assert not adapter.capabilities.supports_mcp_config
    assert adapter.new_session_id() is None


def test_build_command_shape() -> None:
    adapter = _adapter()
    with patch.object(adapter, "locate", return_value="/usr/bin/opencode"):
        cmd = adapter.build_command(_request())
    assert cmd[0] == "/usr/bin/opencode"
    assert cmd[1:5] == ["run", "--format", "json", "-m"]
    assert cmd[5] == "opencode/big-pickle"
    assert cmd[cmd.index("--dir") + 1] == "/tmp"
    assert "--auto" not in cmd
    assert "p" not in cmd


def test_build_command_auto_when_edits_are_allowed() -> None:
    adapter = _adapter()
    with patch.object(adapter, "locate", return_value="/usr/bin/opencode"):
        cmd = adapter.build_command(_request(allow_edits=True))
    assert "--auto" in cmd


def test_build_command_session_continue_and_effort() -> None:
    adapter = _adapter()
    with patch.object(adapter, "locate", return_value="/usr/bin/opencode"):
        resumed = adapter.build_command(
            _request(resume=True, session_id="ses_abc", effort="high")
        )
        continued = adapter.build_command(_request(resume=True))
    assert resumed[resumed.index("-s") + 1] == "ses_abc"
    assert resumed[resumed.index("--variant") + 1] == "high"
    assert "-c" in continued
    assert "-s" not in continued


def test_parse_model_list_extracts_provider_model_ids() -> None:
    assert _parse_model_list(VERBOSE_MODELS) == [
        "opencode/big-pickle",
        "opencode/hy3-free",
    ]


def test_list_models_falls_back_to_catalog_when_cli_is_missing() -> None:
    adapter = _adapter()
    with patch.object(adapter, "locate", return_value=None):
        models = adapter.list_models()
        assert "opencode/big-pickle" in models
        assert adapter.discover_models() is None


def test_discover_models_parses_verbose_output() -> None:
    adapter = _adapter()
    with (
        patch.object(adapter, "locate", return_value="/usr/bin/opencode"),
        patch("kcia.providers.opencode.subprocess.run", return_value=_completed(VERBOSE_MODELS)),
    ):
        assert adapter.discover_models() == ["opencode/big-pickle", "opencode/hy3-free"]
        assert adapter.list_models() == ["opencode/big-pickle", "opencode/hy3-free"]


def test_parse_auth_list_treats_zero_credentials_as_logged_out() -> None:
    text = "┌  Credentials ~/.local/share/opencode/auth.json\n└  0 credentials\n"
    assert _parse_auth_list(text, "", 0) is AuthStatus.NOT_AUTHENTICATED


def test_parse_auth_list_treats_a_positive_count_as_logged_in() -> None:
    text = "┌  Credentials ~/.local/share/opencode/auth.json\n└  2 credentials\n"
    assert _parse_auth_list(text, "", 0) is AuthStatus.AUTHENTICATED


def test_parse_auth_list_reads_json_providers() -> None:
    assert (
        _parse_auth_list(json.dumps({"providers": ["anthropic"]}), "", 0)
        is AuthStatus.AUTHENTICATED
    )
    assert _parse_auth_list(json.dumps({"providers": []}), "", 0) is AuthStatus.NOT_AUTHENTICATED


def test_parse_auth_list_unknown_on_unparseable_success() -> None:
    assert _parse_auth_list("garbled tui", "", 0) is AuthStatus.UNKNOWN
    assert _parse_auth_list("garbled tui", "", 1) is AuthStatus.NOT_AUTHENTICATED


def test_check_auth_not_installed() -> None:
    adapter = _adapter()
    with patch.object(adapter, "locate", return_value=None):
        assert adapter.check_auth() is AuthStatus.NOT_INSTALLED


def test_check_auth_timeout_is_unknown() -> None:
    adapter = _adapter()
    with (
        patch.object(adapter, "locate", return_value="/usr/bin/opencode"),
        patch(
            "kcia.providers.opencode.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="opencode", timeout=30),
        ),
    ):
        assert adapter.check_auth() is AuthStatus.UNKNOWN


def test_account_lists_provider_names() -> None:
    adapter = _adapter()
    stdout = "┌  Credentials\n●  anthropic\n●  openai\n"
    with (
        patch.object(adapter, "locate", return_value="/usr/bin/opencode"),
        patch("kcia.providers.opencode.subprocess.run", return_value=_completed(stdout)),
    ):
        assert adapter.account() == "anthropic, openai"


def test_parse_stream_line_events() -> None:
    adapter = _adapter()
    state = StreamState()
    events = []
    lines = [
        json.dumps({"type": "step_start", "sessionID": "ses_abc"}),
        json.dumps({"type": "text", "part": {"text": "Hello"}}),
        json.dumps(
            {
                "type": "tool_use",
                "part": {
                    "tool": "read",
                    "state": {
                        "status": "completed",
                        "input": {"filePath": "/repo/lib/main.dart"},
                    },
                },
            }
        ),
        json.dumps(
            {
                "type": "tool_use",
                "part": {
                    "tool": "write",
                    "state": {
                        "status": "completed",
                        "input": {"filePath": "/repo/lib/new.dart"},
                    },
                },
            }
        ),
        json.dumps(
            {
                "type": "step_finish",
                "part": {
                    "reason": "stop",
                    "tokens": {"input": 10, "output": 4, "cache": {"read": 2}},
                },
            }
        ),
        json.dumps({"type": "error", "error": {"data": {"message": "boom"}}}),
    ]
    for line in lines:
        events.extend(adapter.parse_stream_line(line, state))

    assert state.session_id == "ses_abc"
    assert state.final_text == "Hello"
    assert state.tool_calls == 2
    assert "/repo/lib/main.dart" in state.files_read
    assert "/repo/lib/new.dart" in state.files_written
    assert state.input_tokens == 10
    assert state.output_tokens == 4
    assert state.cached_tokens == 2
    assert any(isinstance(event, TextDelta) and event.text == "Hello" for event in events)
    assert any(isinstance(event, ToolCallStart) and event.name == "read" for event in events)
    assert any(isinstance(event, ToolCallEnd) and event.ok for event in events)
    assert any(isinstance(event, FileRead) and event.path == "/repo/lib/main.dart" for event in events)
    assert any(isinstance(event, FileWrite) and event.path == "/repo/lib/new.dart" for event in events)
    assert any(isinstance(event, UsageUpdate) and event.cached == 2 for event in events)
    assert any(isinstance(event, TurnEnd) for event in events)
    error = next(event for event in events if isinstance(event, ProviderError))
    assert error.message == "boom"
    assert error.fatal is True


def test_parse_stream_line_garbage_returns_empty() -> None:
    adapter = _adapter()
    state = StreamState()
    assert adapter.parse_stream_line("not json at all", state) == []
    assert adapter.parse_stream_line("", state) == []
    assert adapter.parse_stream_line("[]", state) == []
