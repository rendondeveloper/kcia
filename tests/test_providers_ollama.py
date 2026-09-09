"""Ollama adapter: HTTP client, tool sandbox, agent loop, and dispatch."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

from kcia.providers.base import AuthStatus, RunRequest
from kcia.providers.catalog import load_catalog
from kcia.providers.events import (
    FileWrite,
    ProviderError,
    TextDelta,
    ToolCallEnd,
    ToolCallStart,
    TurnEnd,
    UsageUpdate,
)
from kcia.providers.ollama.adapter import OllamaAdapter
from kcia.providers.ollama.client import OllamaClient
from kcia.providers.ollama.loop import run_agent_loop
from kcia.providers.ollama.tools import execute_tool, in_edit_scope, safe_path
from kcia.providers.runner import call_provider, run_provider


def _adapter() -> OllamaAdapter:
    return OllamaAdapter(load_catalog()["ollama"])


def _request(**overrides: object) -> RunRequest:
    values: dict = {
        "prompt": "do the thing",
        "model": "qwen3:14b",
        "allow_edits": False,
        "stream": True,
        "workspace_dirs": [Path("/tmp/workspace")],
        "session_id": None,
        "resume": False,
        "effort": None,
        "allowed_tools": None,
        "disallowed_tools": None,
        "cwd": Path("/tmp/workspace"),
        "mcp_config": None,
        "edit_scope": None,
    }
    values.update(overrides)
    return RunRequest(**values)


def _mock_client(handlers: dict[str, httpx.MockTransport]) -> OllamaClient:
    def _transport(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        handler = handlers.get(path)
        if handler is None:
            return httpx.Response(404, json={"error": "not found"})
        return handler(request)

    client = httpx.Client(
        base_url="http://127.0.0.1:11434",
        transport=httpx.MockTransport(_transport),
    )
    return OllamaClient(base_url="http://127.0.0.1:11434", client=client)


def test_capabilities_and_locate() -> None:
    adapter = _adapter()
    assert adapter.id == "ollama"
    assert adapter.capabilities.supports_streaming
    assert not adapter.capabilities.supports_sessions
    assert adapter.capabilities.supports_effort
    assert adapter.capabilities.supports_tool_restriction
    assert not adapter.capabilities.supports_mcp_config
    assert adapter.new_session_id() is None


def test_discover_models_parses_colon_ids() -> None:
    adapter = _adapter()

    def tags_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "models": [
                    {"name": "qwen3:14b"},
                    {"name": "devstral-small-2:24b"},
                ]
            },
        )

    client = _mock_client({"/api/tags": tags_handler, "/api/version": _version_handler})
    with patch.object(adapter, "_get_client", return_value=client):
        assert adapter.discover_models() == ["qwen3:14b", "devstral-small-2:24b"]


def test_check_auth_unreachable() -> None:
    adapter = _adapter()

    def fail_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    client = httpx.Client(
        base_url="http://127.0.0.1:11434",
        transport=httpx.MockTransport(fail_handler),
    )
    ollama = OllamaClient(base_url="http://127.0.0.1:11434", client=client)
    with patch.object(adapter, "_get_client", return_value=ollama):
        assert adapter.check_auth() is AuthStatus.NOT_INSTALLED
        assert adapter.locate() is None


def test_account_reports_host_and_version() -> None:
    adapter = _adapter()
    client = _mock_client(
        {
            "/api/version": _version_handler,
            "/api/tags": _tags_handler,
        }
    )
    with patch.object(adapter, "_get_client", return_value=client):
        assert adapter.account() == "http://127.0.0.1:11434 (ollama 0.5.0)"


def test_build_command_raises() -> None:
    adapter = _adapter()
    with pytest.raises(NotImplementedError, match="in-process"):
        adapter.build_command(_request())


def test_safe_path_rejects_escape() -> None:
    root = Path("/tmp/workspace")
    assert safe_path(root, "../outside.txt") is None


def test_write_refused_without_allow_edits(tmp_path: Path) -> None:
    target = tmp_path / "out.txt"
    ok, message = execute_tool(
        "write_file",
        {"path": "out.txt", "content": "x"},
        _request(allow_edits=False, cwd=tmp_path),
    )
    assert not ok
    assert "not allowed" in message
    assert not target.exists()


def test_write_refused_outside_edit_scope(tmp_path: Path) -> None:
    ok, message = execute_tool(
        "write_file",
        {"path": "src/main.py", "content": "x"},
        _request(allow_edits=True, cwd=tmp_path, edit_scope=(".ai/**",)),
    )
    assert not ok
    assert "edit scope" in message


def test_write_allowed_inside_edit_scope(tmp_path: Path) -> None:
    (tmp_path / ".ai").mkdir()
    ok, message = execute_tool(
        "write_file",
        {"path": ".ai/plan.md", "content": "plan"},
        _request(allow_edits=True, cwd=tmp_path, edit_scope=(".ai/**",)),
    )
    assert ok
    assert (tmp_path / ".ai" / "plan.md").read_text(encoding="utf-8") == "plan"


def test_malformed_tool_arguments_return_error(tmp_path: Path) -> None:
    ok, message = execute_tool("read_file", "not-an-object", _request(cwd=tmp_path))
    assert not ok
    assert "JSON object" in message


def test_agent_loop_streams_text_and_usage() -> None:
    chunks = [
        json.dumps({"message": {"role": "assistant", "content": "Hi"}, "done": False}),
        json.dumps(
            {
                "message": {"role": "assistant", "content": "!"},
                "done": True,
                "prompt_eval_count": 12,
                "eval_count": 3,
            }
        ),
    ]

    client = _mock_client(
        {
            "/api/version": _version_handler,
            "/api/tags": _tags_handler,
            "/api/chat": _chat_handler(chunks),
        }
    )
    collected: list[object] = []
    result = run_agent_loop(
        client,
        _request(),
        on_event=collected.append,
    )
    assert result.exit_code == 0
    assert result.output_text == "Hi!"
    assert any(isinstance(event, TextDelta) for event in collected)
    assert any(isinstance(event, UsageUpdate) for event in collected)
    assert any(isinstance(event, TurnEnd) for event in collected)


def test_agent_loop_handles_tool_call_and_malformed_args(tmp_path: Path) -> None:
    (tmp_path / "note.txt").write_text("hello", encoding="utf-8")
    chunks_round_one = [
        json.dumps(
            {
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "read_file",
                                "arguments": "not-json",
                            }
                        }
                    ],
                },
                "done": True,
                "prompt_eval_count": 5,
                "eval_count": 1,
            }
        ),
    ]
    chunks_round_two = [
        json.dumps(
            {
                "message": {"role": "assistant", "content": "done"},
                "done": True,
                "prompt_eval_count": 8,
                "eval_count": 2,
            }
        ),
    ]
    chat_calls = {"count": 0}

    def chat_handler(request: httpx.Request) -> httpx.Response:
        chat_calls["count"] += 1
        lines = chunks_round_one if chat_calls["count"] == 1 else chunks_round_two
        return _stream_response(lines)

    client = _mock_client(
        {
            "/api/version": _version_handler,
            "/api/tags": _tags_handler,
            "/api/chat": chat_handler,
        }
    )
    result = run_agent_loop(client, _request(cwd=tmp_path))
    assert result.exit_code == 0
    assert result.output_text == "done"
    assert result.tool_calls >= 1


def test_agent_loop_fatal_when_model_missing() -> None:
    client = _mock_client(
        {
            "/api/version": _version_handler,
            "/api/tags": _tags_handler,
            "/api/chat": _chat_handler([]),
        }
    )
    collected: list[object] = []
    result = run_agent_loop(
        client,
        _request(model="missing:tag"),
        on_event=collected.append,
    )
    assert result.exit_code == 1
    assert any(isinstance(event, ProviderError) and event.fatal for event in collected)


def test_call_provider_prefers_adapter_run() -> None:
    adapter = _adapter()
    adapter.run = MagicMock(return_value=MagicMock(exit_code=0, output_text="ok"))
    result = call_provider(run_provider, adapter, _request())
    adapter.run.assert_called_once()
    assert result.output_text == "ok"


def test_call_provider_respects_injected_runner() -> None:
    adapter = _adapter()
    adapter.run = MagicMock()

    def injected(adapter_arg: object, req: RunRequest) -> MagicMock:
        assert adapter_arg is adapter
        mock = MagicMock(exit_code=0, output_text="injected")
        return mock

    result = call_provider(injected, adapter, _request())
    adapter.run.assert_not_called()
    assert result.output_text == "injected"


def test_model_in_catalog_live_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from kcia.config import model_in_catalog, set_agent

    config_dir = tmp_path / "config" / "kcia"
    config_dir.mkdir(parents=True)
    config_file = config_dir / "config.yaml"
    monkeypatch.setattr("kcia.config.GLOBAL_CONFIG_DIR", config_dir)
    monkeypatch.setattr("kcia.config.GLOBAL_CONFIG_FILE", config_file)

    assert model_in_catalog("ollama", "custom:7b")
    setting = set_agent("planner", "ollama", model="custom:7b", scope="global")
    assert setting.model == "custom:7b"


def test_in_edit_scope_gitwildmatch() -> None:
    assert in_edit_scope(".ai/plan.md", (".ai/**",))
    assert not in_edit_scope("src/main.py", (".ai/**",))
    assert in_edit_scope("src/main.py", ("**",))


def _version_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"version": "0.5.0"})


def _tags_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        json={"models": [{"name": "qwen3:14b"}, {"name": "devstral-small-2:24b"}]},
    )


def _chat_handler(lines: list[str]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        return _stream_response(lines)

    return handler


def _stream_response(lines: list[str]) -> httpx.Response:
    body = "\n".join(lines)
    return httpx.Response(200, content=body.encode("utf-8"))
