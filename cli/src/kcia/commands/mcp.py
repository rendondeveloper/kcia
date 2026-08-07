"""Register and manage MCP servers for this repository."""

from __future__ import annotations

from typing import Optional

import typer

from kcia.mcp.catalog import load_mcp_catalog
from kcia.mcp.config import (
    config_path,
    load_enabled,
    render_cursor_config,
    resolve_enabled,
    save_enabled,
)
from kcia.paths import find_repo_root

app = typer.Typer(help="Register and manage MCP servers.", no_args_is_help=True)


def _repo() -> "object":
    repo = find_repo_root()
    if repo is None:
        typer.echo("No git repository found. MCP servers are enabled per repository.")
        raise typer.Exit(code=1)
    return repo


@app.command("catalog")
def mcp_catalog() -> None:
    """List the servers kcia knows about."""
    catalog = load_mcp_catalog()
    if not catalog:
        typer.echo("No MCP catalog found. Check the control plane installation.")
        raise typer.Exit(code=1)
    for server in catalog.values():
        roles = ", ".join(server.roles) if server.roles else "all roles"
        typer.echo(f"{server.id} ({server.display_name}) — {roles}")
        if server.description:
            typer.echo(f"  {server.description}")
        typer.echo(f"  {server.transport} {server.url}")


@app.command("add")
def mcp_add(
    server_id: str = typer.Argument(..., help="Server id from `kcia mcp catalog`."),
) -> None:
    """Enable a server for this repository."""
    repo = _repo()
    catalog = load_mcp_catalog()
    if server_id not in catalog:
        known = ", ".join(sorted(catalog)) or "none"
        typer.echo(f"Unknown server `{server_id}`. Available: {known}")
        raise typer.Exit(code=1)

    server = catalog[server_id]
    enabled = load_enabled(repo)
    enabled[server_id] = enabled.get(server_id) or {}
    save_enabled(repo, enabled)

    typer.echo(f"Enabled `{server_id}` in {config_path(repo)}.")
    written = render_cursor_config(repo)
    if written:
        typer.echo(f"Wrote {written}")

    if server.cloud_only:
        typer.echo("Note: this server supports Atlassian Cloud only, not Server/Data Center.")
    if server.verify_hint:
        typer.echo(f"Verify: {server.verify_hint.strip()}")
    typer.echo("")
    typer.echo("kcia does not store credentials — log in with the provider CLI:")
    typer.echo(f"  {server.auth_hint.strip()}")


@app.command("remove")
def mcp_remove(server_id: str = typer.Argument(..., help="Server id to disable.")) -> None:
    """Disable a server for this repository."""
    repo = _repo()
    enabled = load_enabled(repo)
    if server_id not in enabled:
        typer.echo(f"`{server_id}` is not enabled here.")
        raise typer.Exit(code=1)
    enabled.pop(server_id)
    save_enabled(repo, enabled)
    render_cursor_config(repo)
    typer.echo(f"Disabled `{server_id}`.")


@app.command("list")
def mcp_list(
    role: Optional[str] = typer.Option(None, "--role", help="Show what one role can see."),
) -> None:
    """Show the servers enabled for this repository."""
    repo = _repo()
    entries = resolve_enabled(repo)
    if not entries:
        typer.echo("No MCP servers enabled. See `kcia mcp catalog`.")
        return

    for entry in entries:
        server = entry.server
        roles = ", ".join(server.roles) if server.roles else "all roles"
        if role and not server.allows(role):
            typer.echo(f"{server.id}\thidden from {role} (allowed: {roles})")
            continue
        typer.echo(f"{server.id}\t{server.transport}\t{roles}")

    unknown = set(load_enabled(repo)) - {entry.server.id for entry in entries}
    for server_id in sorted(unknown):
        typer.echo(f"{server_id}\twarning: enabled but not in the catalog; ignored")
