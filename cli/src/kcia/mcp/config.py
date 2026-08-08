"""Per-repository MCP enablement, and rendering it for each provider.

The two provider CLIs differ in a way that decides the design:

* Claude Code takes `--mcp-config <file>` per invocation, so kcia can hand each
  wave exactly the servers its role is allowed to see.
* Cursor reads `.cursor/mcp.json` for the whole repository, with no per-run
  override, so role gating there is declarative only.

Credentials are never stored by kcia: the provider CLI owns the session.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import yaml

from kcia.mcp.catalog import McpServer, load_mcp_catalog

CONFIG_SCHEMA_VERSION = 1
CURSOR_CONFIG = Path(".cursor") / "mcp.json"


@dataclass(frozen=True)
class EnabledServer:
    server: McpServer
    settings: dict[str, object]


def config_path(repo_root: Path) -> Path:
    return repo_root / ".ai" / "mcp.yaml"


def load_enabled(repo_root: Path) -> dict[str, dict[str, object]]:
    path = config_path(repo_root)
    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    servers = data.get("servers") or {}
    return {key: (value or {}) for key, value in servers.items()}


def save_enabled(repo_root: Path, servers: dict[str, dict[str, object]]) -> Path:
    path = config_path(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(
            {"schema_version": CONFIG_SCHEMA_VERSION, "servers": servers},
            sort_keys=True,
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    return path


def resolve_enabled(repo_root: Path) -> list[EnabledServer]:
    """Enabled servers that still exist in the catalog, in catalog order."""
    catalog = load_mcp_catalog()
    enabled = load_enabled(repo_root)
    return [
        EnabledServer(server=catalog[server_id], settings=settings)
        for server_id, settings in enabled.items()
        if server_id in catalog
    ]


def servers_for_role(repo_root: Path, role: str) -> list[EnabledServer]:
    return [entry for entry in resolve_enabled(repo_root) if entry.server.allows(role)]


def allowed_tools_for_role(repo_root: Path, role: str) -> list[str]:
    """Tool names the role may call, across its enabled servers."""
    tools: list[str] = []
    for entry in servers_for_role(repo_root, role):
        tools.extend(entry.server.allowed_tools)
    return tools


def _server_payload(entry: EnabledServer) -> dict[str, object]:
    payload: dict[str, object] = {"type": entry.server.transport, "url": entry.server.url}
    headers = entry.settings.get("headers")
    if isinstance(headers, dict) and headers:
        payload["headers"] = headers
    return payload


def render_claude_config(repo_root: Path, role: str, destination: Path) -> Path | None:
    """Write the `--mcp-config` file for one role, or None when it has no servers."""
    entries = servers_for_role(repo_root, role)
    if not entries:
        return None
    payload = {"mcpServers": {entry.server.id: _server_payload(entry) for entry in entries}}
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return destination


def render_cursor_config(repo_root: Path) -> Path | None:
    """Write `.cursor/mcp.json` with every enabled server.

    Cursor has no per-run override, so this cannot be gated by role. Servers that
    only one role should use are still written here; the gating is documented,
    not enforced.
    """
    entries = resolve_enabled(repo_root)
    path = repo_root / CURSOR_CONFIG
    if not entries:
        return None
    payload = {"mcpServers": {entry.server.id: _server_payload(entry) for entry in entries}}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path
