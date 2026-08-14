"""MCP server registration, per-role gating, and provider wiring."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kcia.mcp.catalog import load_mcp_catalog
from kcia.mcp.config import (
    CURSOR_CONFIG,
    render_claude_config,
    render_cursor_config,
    resolve_enabled,
    save_enabled,
    servers_for_role,
)
from kcia.providers.base import RunRequest
from kcia.providers.catalog import load_catalog
from kcia.providers.claude import ClaudeAdapter
from kcia.providers.cursor import CursorAdapter


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    return root


def _request(mcp_config: Path | None) -> RunRequest:
    return RunRequest(
        prompt="p",
        model="m",
        allow_edits=False,
        stream=False,
        workspace_dirs=[Path("/tmp")],
        session_id=None,
        resume=False,
        effort=None,
        allowed_tools=None,
        disallowed_tools=None,
        cwd=Path("/tmp"),
        mcp_config=mcp_config,
    )


# --- catalog -----------------------------------------------------------------


def test_catalog_declares_atlassian_as_planner_only() -> None:
    server = load_mcp_catalog()["atlassian"]
    assert server.roles == ("planner",)
    assert server.allows("planner")
    assert not server.allows("builder")
    assert server.cloud_only


def test_every_catalog_entry_has_a_url_and_auth_hint() -> None:
    for server in load_mcp_catalog().values():
        assert server.url, server.id
        assert server.auth_hint, server.id


# --- per-role gating ---------------------------------------------------------


def test_role_gating_filters_the_server_list(repo: Path) -> None:
    save_enabled(repo, {"atlassian": {}})
    assert [e.server.id for e in servers_for_role(repo, "planner")] == ["atlassian"]
    assert servers_for_role(repo, "builder") == []


def test_claude_config_is_written_only_for_allowed_roles(repo: Path) -> None:
    save_enabled(repo, {"atlassian": {}})

    planner = render_claude_config(repo, "planner", repo / "planner.json")
    assert planner is not None
    payload = json.loads(planner.read_text(encoding="utf-8"))
    assert payload["mcpServers"]["atlassian"]["type"] == "sse"

    assert render_claude_config(repo, "builder", repo / "builder.json") is None
    assert not (repo / "builder.json").exists()


def test_servers_missing_from_the_catalog_are_ignored(repo: Path) -> None:
    """A stale entry must not crash a run or silently become a real server."""
    save_enabled(repo, {"atlassian": {}, "removed-upstream": {}})
    assert [e.server.id for e in resolve_enabled(repo)] == ["atlassian"]


def test_nothing_enabled_yields_no_config(repo: Path) -> None:
    assert servers_for_role(repo, "planner") == []
    assert render_claude_config(repo, "planner", repo / "x.json") is None
    assert render_cursor_config(repo) is None


# --- provider wiring ---------------------------------------------------------


def test_claude_passes_strict_mcp_config() -> None:
    """Without --strict-mcp-config the CLI also loads globally registered
    servers, which would defeat the role gating."""
    adapter = ClaudeAdapter(load_catalog()["claude"])
    cmd = adapter.build_command(_request(Path("/tmp/mcp.json")))
    assert "--mcp-config" in cmd
    assert cmd[cmd.index("--mcp-config") + 1] == "/tmp/mcp.json"
    assert "--strict-mcp-config" in cmd


def test_claude_omits_the_flag_when_there_are_no_servers() -> None:
    adapter = ClaudeAdapter(load_catalog()["claude"])
    assert "--mcp-config" not in adapter.build_command(_request(None))


def test_cursor_auto_approves_so_print_mode_does_not_hang() -> None:
    adapter = CursorAdapter(load_catalog()["cursor"])
    assert "--approve-mcps" in adapter.build_command(_request(Path("/tmp/mcp.json")))
    assert "--approve-mcps" not in adapter.build_command(_request(None))


def test_cursor_config_lands_in_the_repository(repo: Path) -> None:
    save_enabled(repo, {"atlassian": {}})
    written = render_cursor_config(repo)
    assert written == repo / CURSOR_CONFIG
    assert "atlassian" in json.loads(written.read_text(encoding="utf-8"))["mcpServers"]


def test_cursor_config_is_gitignored_because_it_can_hold_headers() -> None:
    from kcia.commands.init import GITIGNORE_ENTRIES

    assert ".cursor/mcp.json" in GITIGNORE_ENTRIES


def test_headers_are_carried_through_when_declared(repo: Path) -> None:
    save_enabled(repo, {"sentry": {"headers": {"Authorization": "Bearer x"}}})
    written = render_claude_config(repo, "planner", repo / "planner.json")
    assert written is not None
    payload = json.loads(written.read_text(encoding="utf-8"))
    assert payload["mcpServers"]["sentry"]["headers"] == {"Authorization": "Bearer x"}


def test_run_wave_gives_the_planner_its_servers_and_the_builder_none(tmp_path: Path) -> None:
    """End to end: the gating must reach the command the provider runs."""
    import shutil
    import subprocess

    from kcia.providers.base import RunResult
    from kcia.waves.runner import run_wave
    from kcia.waves.session import Session

    root = Path(__file__).resolve().parents[1]
    repo = tmp_path / "melos_mono"
    shutil.copytree(root / "tests" / "fixtures" / "repos" / "melos_mono", repo)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(
        [str(root / ".venv" / "bin" / "kcia"), "start", "--yes", "--path", str(repo)],
        check=True,
        capture_output=True,
    )
    save_enabled(repo, {"atlassian": {}})

    Session.create(repo, text="fix it", mode="prompt")
    session = Session.load(repo)

    seen: dict[str, Path | None] = {}

    def capture(_adapter, req, **_kwargs):
        seen[req.model] = req.mcp_config
        return RunResult(output_text="# out\n\ndone", exit_code=0)

    run_wave("understanding", session, provider_runner=capture)
    planner_config = next(iter(seen.values()))
    assert planner_config is not None and planner_config.is_file()
    assert "atlassian" in planner_config.read_text(encoding="utf-8")

    seen.clear()
    session = Session.load(repo)
    run_wave("documentation-final", session, force=True, provider_runner=capture)
    assert next(iter(seen.values())) is None, "builder must not receive planner-only servers"


def test_claude_allowlists_mcp_tools_because_print_mode_denies_them() -> None:
    """Without --allowed-tools every MCP call is denied non-interactively."""
    adapter = ClaudeAdapter(load_catalog()["claude"])
    cmd = adapter.build_command(
        RunRequest(
            prompt="p", model="m", allow_edits=False, stream=False,
            workspace_dirs=[Path("/tmp")], session_id=None, resume=False, effort=None,
            allowed_tools=None, disallowed_tools=None, cwd=Path("/tmp"),
            mcp_config=Path("/tmp/mcp.json"), mcp_tools=["mcp__atlassian__getJiraIssue"],
        )
    )
    assert "--allowed-tools" in cmd
    assert "mcp__atlassian__getJiraIssue" in cmd


def test_atlassian_allowlist_excludes_every_write_tool() -> None:
    """The guardrails forbid commenting and transitioning; naming individual
    read tools is what makes that a restriction instead of a request."""
    tools = load_mcp_catalog()["atlassian"].allowed_tools
    assert tools, "an empty allowlist would deny every call"
    forbidden = ("create", "edit", "addComment", "addWorklog", "transitionJira", "update")
    offenders = [t for t in tools if any(word.lower() in t.lower() for word in forbidden)]
    assert offenders == [], offenders
    assert "mcp__atlassian__getJiraIssue" in tools
    # Granting the whole server would pull the write tools back in.
    assert "mcp__atlassian" not in tools
