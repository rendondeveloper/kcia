"""Provider and agent configuration tests — Fase 2 acceptance criteria."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from kcia.config import (
    GLOBAL_CONFIG_FILE,
    load_global_config,
    load_repo_agents,
    resolve_agents,
    set_agent,
    swap_agents,
)
from kcia.providers.base import RunRequest
from kcia.providers.claude import ClaudeAdapter
from kcia.providers.catalog import load_catalog
from kcia.providers.events import (
    FileRead,
    StreamState,
    TextDelta,
    ToolCallStart,
    TurnEnd,
    UsageUpdate,
)
from kcia.providers.registry import get_adapter

ROOT = Path(__file__).resolve().parents[1]
KCIA = ROOT / ".venv" / "bin" / "kcia"
STREAM_FIXTURE = ROOT / "tests" / "fixtures" / "providers" / "claude_stream.jsonl"


@pytest.fixture
def isolated_global_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    config_dir = tmp_path / "config" / "kcia"
    config_dir.mkdir(parents=True)
    config_file = config_dir / "config.yaml"
    monkeypatch.setattr("kcia.config.GLOBAL_CONFIG_DIR", config_dir)
    monkeypatch.setattr("kcia.config.GLOBAL_CONFIG_FILE", config_file)
    return config_file


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    return repo


def test_claude_build_command_disallows_edits() -> None:
    adapter = ClaudeAdapter(load_catalog()["claude"])
    req = RunRequest(
        prompt="test",
        model="claude-sonnet-5",
        allow_edits=False,
        stream=True,
        workspace_dirs=[Path("/tmp")],
        session_id=None,
        resume=False,
        effort=None,
        allowed_tools=None,
        disallowed_tools=None,
        cwd=Path("/tmp"),
    )
    cmd = adapter.build_command(req)
    assert "--disallowed-tools" in cmd
    idx = cmd.index("--disallowed-tools")
    disallowed = cmd[idx + 1 : idx + 4]
    assert "Edit" in disallowed
    assert "Write" in disallowed
    assert "NotebookEdit" in disallowed
    assert "--permission-mode" in cmd
    assert cmd[cmd.index("--permission-mode") + 1] == "default"


def test_claude_parse_stream_line_fixture() -> None:
    adapter = ClaudeAdapter(load_catalog()["claude"])
    state = StreamState()
    events = []
    for line in STREAM_FIXTURE.read_text(encoding="utf-8").splitlines():
        events.extend(adapter.parse_stream_line(line, state))

    assert any(isinstance(event, TextDelta) for event in events)
    assert any(isinstance(event, ToolCallStart) for event in events)
    assert any(isinstance(event, FileRead) for event in events)
    assert any(isinstance(event, UsageUpdate) for event in events)
    assert any(isinstance(event, TurnEnd) for event in events)
    assert state.session_id == "sess-abc-123"
    assert state.final_text == "Hello world"


def test_claude_parse_stream_line_garbage_returns_empty() -> None:
    adapter = ClaudeAdapter(load_catalog()["claude"])
    state = StreamState()
    assert adapter.parse_stream_line("not json at all", state) == []
    assert adapter.parse_stream_line("", state) == []


def test_agent_set_persists_global(isolated_global_config: Path) -> None:
    set_agent("planner", "claude", model="claude-opus-5", scope="global")
    config = load_global_config()
    assert config["agents"]["planner"]["provider"] == "claude"
    assert config["agents"]["planner"]["model"] == "claude-opus-5"

    resolved = resolve_agents()
    assert resolved["planner"].provider == "claude"
    assert resolved["planner"].model == "claude-opus-5"
    assert resolved["planner"].origin == "global"


def test_agent_set_repo_scope(git_repo: Path, isolated_global_config: Path) -> None:
    set_agent(
        "builder",
        "cursor",
        model="composer-2.5",
        scope="repo",
        repo_root=git_repo,
    )
    agents = load_repo_agents(git_repo)
    assert agents["builder"]["provider"] == "cursor"
    assert agents["builder"]["model"] == "composer-2.5"
    assert (git_repo / ".ai" / "local" / "agents.yaml").is_file()

    resolved = resolve_agents(git_repo)
    assert resolved["builder"].origin == "repo"
    assert resolved["builder"].provider == "cursor"


def test_repo_local_is_gitignored() -> None:
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert ".ai/local/" in gitignore


def test_agent_swap_exchanges_provider_model_effort(
    isolated_global_config: Path,
) -> None:
    set_agent("planner", "claude", model="claude-opus-5", effort="high", scope="global")
    set_agent("builder", "cursor", model="composer-2.5", effort="low", scope="global")
    swap_agents(scope="global")

    resolved = resolve_agents()
    assert resolved["planner"].provider == "cursor"
    assert resolved["planner"].model == "composer-2.5"
    assert resolved["planner"].effort == "low"
    assert resolved["builder"].provider == "claude"
    assert resolved["builder"].model == "claude-opus-5"
    assert resolved["builder"].effort == "high"


def test_invalid_model_raises() -> None:
    with pytest.raises(ValueError, match="unknown model"):
        set_agent("planner", "claude", model="modelo-inventado", scope="global")


def test_unknown_provider_raises() -> None:
    with pytest.raises(ValueError, match="unknown provider"):
        set_agent("planner", "no-such-provider", scope="global")


def test_get_adapter_unknown_provider() -> None:
    with pytest.raises(KeyError, match="unknown provider"):
        get_adapter("missing-provider")


def test_agent_models_lists_catalog() -> None:
    result = subprocess.run(
        [str(KCIA), "agent", "models", "claude"],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert result.returncode == 0
    assert "claude-sonnet-5" in result.stdout
    assert "default" in result.stdout
    # Tier and best_for come from the catalog and must reach the user.
    assert "balanced" in result.stdout
    assert "best for: implementation, review" in result.stdout


def test_agent_models_json_lists_tier_and_best_for() -> None:
    result = subprocess.run(
        [str(KCIA), "agent", "models", "claude", "--json"],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["claude"]["default_model"] == "claude-sonnet-5"
    sonnet = next(m for m in payload["claude"]["models"] if m["id"] == "claude-sonnet-5")
    assert sonnet["tier"] == "balanced"
    assert sonnet["best_for"] == ["implementation", "review"]


def test_agent_models_rejects_unknown_provider() -> None:
    result = subprocess.run(
        [str(KCIA), "agent", "models", "does-not-exist"],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert result.returncode == 1
    assert "Available: claude, cursor, opencode" in result.stdout


def test_catalog_opencode_models_use_real_ids() -> None:
    """Ids resolved with `opencode models --verbose`; they are `provider/model`."""
    entry = load_catalog()["opencode"]
    ids = [model.id for model in entry.models]
    assert "opencode/big-pickle" in ids
    assert "opencode/mimo-v2.5-free" in ids
    assert entry.default_model == "opencode/big-pickle"
    adapter = get_adapter("opencode")
    assert adapter.id == "opencode"
    assert adapter.executable == "opencode"
    assert adapter.capabilities.supports_effort
    assert adapter.capabilities.supports_sessions
    assert adapter.capabilities.supports_streaming
    assert not adapter.capabilities.supports_tool_restriction
    assert not adapter.capabilities.supports_mcp_config
    assert adapter.new_session_id() is None


def test_catalog_cursor_models_use_real_ids() -> None:
    """Cursor uses its own ids, not Anthropic API ids; `claude-sonnet-5` is not one."""
    ids = [model.id for model in load_catalog()["cursor"].models]
    assert "auto" in ids
    assert "composer-2.5" in ids
    assert "claude-sonnet-5" not in ids
    assert "gpt-5.5" not in ids


def test_every_catalog_default_is_one_of_its_own_models() -> None:
    for provider_id, entry in load_catalog().items():
        ids = [model.id for model in entry.models]
        assert entry.default_model in ids, f"{provider_id} default is not in its model list"


def test_model_in_catalog_detects_stale_configuration() -> None:
    from kcia.config import model_in_catalog

    assert model_in_catalog("cursor", "composer-2.5")
    assert not model_in_catalog("cursor", "claude-sonnet-5")
    assert not model_in_catalog("no-such-provider", "auto")


def test_cursor_parses_its_list_models_output() -> None:
    from kcia.providers.cursor import _parse_model_list

    stdout = (
        "Available models\n"
        "\n"
        "auto - Auto (default)\n"
        "composer-2.5 - Composer 2.5\n"
        "claude-opus-5-thinking-high - Opus 5 1M Thinking\n"
        "\n"
        "Tip: use --model <id> (or /model <id> in interactive mode) to switch.\n"
    )
    assert _parse_model_list(stdout) == [
        "auto",
        "composer-2.5",
        "claude-opus-5-thinking-high",
    ]
