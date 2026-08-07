"""Configure planner and builder agents."""

from __future__ import annotations

import json
from typing import Literal, Optional

import typer

from kcia.config import (
    AgentScope,
    model_in_catalog,
    resolve_agents,
    set_agent,
    swap_agents,
)
from kcia.paths import find_repo_root
from kcia.providers.catalog import load_catalog
from kcia.providers.registry import AGENT_ROLES, build_registry, is_provider_installed

app = typer.Typer(help="Configure planner and builder agents.", no_args_is_help=True)

Role = Literal["planner", "builder"]


@app.command("show")
def agent_show(
    as_json: bool = typer.Option(False, "--json", help="Output as JSON."),
) -> None:
    repo_root = find_repo_root()
    resolved = resolve_agents(repo_root)
    if as_json:
        payload = {
            role: {
                "provider": item.provider,
                "model": item.model,
                "effort": item.effort,
                "origin": item.origin,
            }
            for role, item in resolved.items()
        }
        typer.echo(json.dumps(payload, indent=2))
        return

    for role in AGENT_ROLES:
        item = resolved[role]
        typer.echo(f"{role}:")
        typer.echo(f"  provider: {item.provider}")
        typer.echo(f"  model: {item.model}")
        if item.effort:
            typer.echo(f"  effort: {item.effort}")
        typer.echo(f"  origin: {item.origin}")
        if not model_in_catalog(item.provider, item.model):
            typer.echo(
                f"  warning: `{item.model}` is not offered by `{item.provider}` anymore; "
                f"run `kcia agent models {item.provider}`"
            )


@app.command("set")
def agent_set(
    role: Role = typer.Argument(..., help="Agent role: planner or builder."),
    provider: str = typer.Argument(..., help="Provider id (claude, cursor, …)."),
    model: Optional[str] = typer.Option(None, "--model", "-m", help="Model id."),
    effort: Optional[str] = typer.Option(
        None,
        "--effort",
        "-e",
        help="Effort level: low, medium, or high.",
    ),
    scope: AgentScope = typer.Option(
        "global",
        "--scope",
        help="Persist globally or in the current repo.",
    ),
) -> None:
    if role not in AGENT_ROLES:
        typer.echo(f"Unknown role `{role}`. Choose from: {', '.join(AGENT_ROLES)}")
        raise typer.Exit(code=1)

    repo_root = find_repo_root() if scope == "repo" else None
    if scope == "repo" and repo_root is None:
        typer.echo("No git repository found; cannot use --scope repo.")
        raise typer.Exit(code=1)

    try:
        setting = set_agent(
            role,
            provider,
            model=model,
            effort=effort,
            scope=scope,
            repo_root=repo_root,
        )
    except ValueError as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1) from exc

    if not is_provider_installed(provider):
        catalog = load_catalog()
        hint = catalog[provider].install_hint
        typer.echo(f"warning: provider `{provider}` is not installed. {hint}")

    location = "global config" if scope == "global" else f"{repo_root}/.ai/local/agents.yaml"
    typer.echo(f"Set {role} to {setting.provider}/{setting.model} in {location}.")


@app.command("swap")
def agent_swap(
    scope: AgentScope = typer.Option(
        "global",
        "--scope",
        help="Swap agents in global or repo config.",
    ),
) -> None:
    repo_root = find_repo_root() if scope == "repo" else None
    if scope == "repo" and repo_root is None:
        typer.echo("No git repository found; cannot use --scope repo.")
        raise typer.Exit(code=1)
    swap_agents(scope=scope, repo_root=repo_root)
    typer.echo(f"Swapped planner and builder in {scope} config.")


@app.command("models")
def agent_models(
    provider: Optional[str] = typer.Argument(None, help="Filter by provider id."),
    as_json: bool = typer.Option(False, "--json", help="Output as JSON."),
    live: bool = typer.Option(
        False,
        "--live",
        help="Ask the installed CLI which models it offers and flag catalog drift.",
    ),
) -> None:
    catalog = load_catalog()
    registry = build_registry()
    providers = [provider] if provider else sorted(catalog)

    for provider_id in providers:
        if provider_id not in catalog:
            known = ", ".join(sorted(catalog))
            typer.echo(f"Unknown provider `{provider_id}`. Available: {known}")
            raise typer.Exit(code=1)

    def _installed(provider_id: str) -> bool:
        return provider_id in registry and registry[provider_id].locate() is not None

    if as_json:
        payload = {
            provider_id: {
                "display_name": catalog[provider_id].display_name,
                "executable": catalog[provider_id].executable,
                "installed": _installed(provider_id),
                "default_model": catalog[provider_id].default_model,
                "models": [
                    {"id": m.id, "tier": m.tier, "best_for": m.best_for or []}
                    for m in catalog[provider_id].models
                ],
            }
            for provider_id in providers
        }
        typer.echo(json.dumps(payload, indent=2))
        return

    drift = 0
    for provider_id in providers:
        entry = catalog[provider_id]
        status = "installed" if _installed(provider_id) else "not installed"
        typer.echo(f"{provider_id} ({entry.display_name}) [{status}]")

        offered: list[str] | None = None
        if live:
            discover = getattr(registry.get(provider_id), "discover_models", None)
            offered = discover() if callable(discover) else None
            if offered is None:
                typer.echo("  (live check unavailable for this provider)")

        for model in entry.models:
            details = []
            if model.tier:
                details.append(model.tier)
            if model.id == entry.default_model:
                details.append("default")
            if model.best_for:
                details.append("best for: " + ", ".join(model.best_for))
            if offered is not None and model.id not in offered:
                details.append("NOT OFFERED by the installed CLI")
                drift += 1
            suffix = f" — {'; '.join(details)}" if details else ""
            typer.echo(f"  {model.id}{suffix}")
        if not _installed(provider_id) and entry.install_hint:
            typer.echo(f"  install: {entry.install_hint}")
        typer.echo(f"  use: kcia agent set <planner|builder> {provider_id} --model <id>")

    if drift:
        typer.echo("")
        typer.echo(
            f"{drift} catalog entr{'y' if drift == 1 else 'ies'} no longer exist upstream. "
            "Update control-plane/providers/catalog.yaml."
        )
        raise typer.Exit(code=1)
