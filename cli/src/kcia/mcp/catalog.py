"""Known MCP servers, declared as control-plane data."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from kcia.paths import control_plane_root

CATALOG_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class McpServer:
    id: str
    display_name: str
    transport: str
    url: str
    description: str = ""
    auth_hint: str = ""
    verify_hint: str = ""
    cloud_only: bool = False
    # Which agent roles may see this server. Empty means every role.
    roles: tuple[str, ...] = field(default_factory=tuple)
    # Tool names to pre-approve. Claude denies every MCP call in `--print` mode
    # without this, and naming individual tools is what keeps write tools out.
    allowed_tools: tuple[str, ...] = field(default_factory=tuple)

    def allows(self, role: str) -> bool:
        return not self.roles or role in self.roles


def catalog_path() -> Path:
    return control_plane_root() / "mcp" / "catalog.yaml"


def load_mcp_catalog() -> dict[str, McpServer]:
    path = catalog_path()
    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    servers: dict[str, McpServer] = {}
    for server_id, raw in (data.get("servers") or {}).items():
        servers[server_id] = McpServer(
            id=server_id,
            display_name=raw.get("display_name", server_id),
            transport=raw.get("transport", "http"),
            url=raw.get("url", ""),
            description=raw.get("description", ""),
            auth_hint=raw.get("auth_hint", ""),
            verify_hint=raw.get("verify_hint", ""),
            cloud_only=bool(raw.get("cloud_only", False)),
            roles=tuple(raw.get("roles") or ()),
            allowed_tools=tuple(raw.get("allowed_tools") or ()),
        )
    return servers
