"""Configure planner and builder agents."""

from __future__ import annotations

import json
from typing import Literal, Optional

import typer

from kcia.config import (
    AgentScope,
    add_agent_fallback,
    model_in_catalog,
    pin_agent,
    resolve_agents,
    set_agent,
    set_agent_active_target,
    swap_agents,
    unpin_agent,
)
from kcia.paths import find_repo_root
from kcia.providers.catalog import load_catalog
from kcia.providers.registry import AGENT_ROLES, build_registry, is_provider_installed

app = typer.Typer(help="Configure planner and builder agents.", no_args_is_help=True)
fallback_app = typer.Typer(help="Manage ordered fallback agents.", no_args_is_help=True)
app.add_typer(fallback_app, name="fallback")

Role = Literal["planner", "builder"]
SwitchTarget = Literal["primary", "fallback"]


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
                "automatic": item.automatic,
                "active_index": item.active_index,
                "manual_pin": item.manual_pin,
                "fallbacks": [
                    {
                        "provider": fallback.provider,
                        "model": fallback.model,
                        "effort": fallback.effort,
                        "billing_profile": fallback.billing_profile,
                    }
                    for fallback in item.fallbacks
                ],
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
        typer.echo(f"  automatic routing: {'on' if item.automatic else 'off'}")
        typer.echo(f"  active target: {_active_label(item)}")
        typer.echo(f"  manual pin: {'on' if item.manual_pin else 'off'}")
        if item.fallbacks:
            typer.echo("  fallbacks:")
            for index, fallback in enumerate(item.fallbacks, start=1):
                suffix = f" ({fallback.billing_profile})" if fallback.billing_profile else ""
                typer.echo(
                    f"    {index}. {fallback.provider}/{fallback.model}{suffix}"
                )
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


@app.command("status")
def agent_status(
    as_json: bool = typer.Option(False, "--json", help="Output as JSON."),
) -> None:
    agent_show(as_json=as_json)


@app.command("switch")
def agent_switch(
    role: Role = typer.Argument(..., help="Agent role: planner or builder."),
    target: SwitchTarget = typer.Option(
        ...,
        "--to",
        help="Switch to primary or fallback.",
    ),
    index: int = typer.Option(
        1,
        "--index",
        help="One-based fallback index when --to fallback.",
    ),
    scope: AgentScope = typer.Option(
        "global",
        "--scope",
        help="Persist globally or in the current repo.",
    ),
) -> None:
    repo_root = find_repo_root() if scope == "repo" else None
    if scope == "repo" and repo_root is None:
        typer.echo("No git repository found; cannot use --scope repo.")
        raise typer.Exit(code=1)
    active_index = 0 if target == "primary" else index
    try:
        set_agent_active_target(
            role,
            active_index=active_index,
            scope=scope,
            repo_root=repo_root,
        )
    except ValueError as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1) from exc
    typer.echo(f"Switched {role} to {_target_label(active_index)} in {scope} config.")


@app.command("pin")
def agent_pin(
    role: Role = typer.Argument(..., help="Agent role: planner or builder."),
    scope: AgentScope = typer.Option(
        "global",
        "--scope",
        help="Persist globally or in the current repo.",
    ),
) -> None:
    repo_root = find_repo_root() if scope == "repo" else None
    if scope == "repo" and repo_root is None:
        typer.echo("No git repository found; cannot use --scope repo.")
        raise typer.Exit(code=1)
    try:
        pin_agent(role, scope=scope, repo_root=repo_root)
    except ValueError as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1) from exc
    typer.echo(f"Pinned {role} to its active target in {scope} config.")


@app.command("unpin")
def agent_unpin(
    role: Role = typer.Argument(..., help="Agent role: planner or builder."),
    scope: AgentScope = typer.Option(
        "global",
        "--scope",
        help="Persist globally or in the current repo.",
    ),
) -> None:
    repo_root = find_repo_root() if scope == "repo" else None
    if scope == "repo" and repo_root is None:
        typer.echo("No git repository found; cannot use --scope repo.")
        raise typer.Exit(code=1)
    unpin_agent(role, scope=scope, repo_root=repo_root)
    typer.echo(f"Unpinned {role} in {scope} config.")


@fallback_app.command("add")
def fallback_add(
    role: Role = typer.Argument(..., help="Agent role: planner or builder."),
    provider: str = typer.Argument(..., help="Provider id."),
    model: Optional[str] = typer.Option(None, "--model", "-m", help="Model id."),
    effort: Optional[str] = typer.Option(None, "--effort", "-e", help="Effort level."),
    billing_profile: Optional[str] = typer.Option(
        None,
        "--billing-profile",
        help="Billing family/account bucket for quota tracking.",
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
        setting = add_agent_fallback(
            role,
            provider,
            model=model,
            effort=effort,
            billing_profile=billing_profile,
            scope=scope,
            repo_root=repo_root,
        )
    except ValueError as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1) from exc
    suffix = f" ({setting.billing_profile})" if setting.billing_profile else ""
    typer.echo(f"Added {role} fallback: {setting.provider}/{setting.model}{suffix}.")


@fallback_app.command("list")
def fallback_list(
    role: Optional[Role] = typer.Argument(None, help="Optional role filter."),
    as_json: bool = typer.Option(False, "--json", help="Output as JSON."),
) -> None:
    repo_root = find_repo_root()
    resolved = resolve_agents(repo_root)
    roles = [role] if role else list(AGENT_ROLES)
    if as_json:
        payload = {
            item.role: [
                {
                    "provider": fallback.provider,
                    "model": fallback.model,
                    "effort": fallback.effort,
                    "billing_profile": fallback.billing_profile,
                }
                for fallback in item.fallbacks
            ]
            for item in (resolved[name] for name in roles)
        }
        typer.echo(json.dumps(payload, indent=2))
        return
    for role_name in roles:
        item = resolved[role_name]
        typer.echo(f"{role_name}:")
        if not item.fallbacks:
            typer.echo("  (no fallbacks configured)")
            continue
        for index, fallback in enumerate(item.fallbacks, start=1):
            suffix = f" ({fallback.billing_profile})" if fallback.billing_profile else ""
            typer.echo(f"  {index}. {fallback.provider}/{fallback.model}{suffix}")


@app.command("models")
def agent_models(
    provider: Optional[str] = typer.Argument(None, help="Filter by provider id."),
    as_json: bool = typer.Option(False, "--json", help="Output as JSON."),
    profile: Optional[str] = typer.Option(
        None,
        "--profile",
        help="Model profile to show, for example `go` for OpenCode Go.",
    ),
    all_models: bool = typer.Option(
        False,
        "--all",
        help="Show every catalog model instead of the curated default profile.",
    ),
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

    def _visible_models(provider_id: str):
        models = catalog[provider_id].models
        selected_profile = profile
        if provider_id == "opencode" and not all_models and selected_profile is None:
            selected_profile = "go"
        if selected_profile is None:
            return models
        return [model for model in models if model.profile == selected_profile]

    if as_json:
        payload = {
            provider_id: {
                "display_name": catalog[provider_id].display_name,
                "executable": catalog[provider_id].executable,
                "installed": _installed(provider_id),
                "default_model": catalog[provider_id].default_model,
                "models": [
                    {
                        "id": m.id,
                        "tier": m.tier,
                        "best_for": m.best_for or [],
                        "num_ctx": m.num_ctx,
                        "billing_profile": m.billing_profile,
                        "roles": m.roles or [],
                        "profile": m.profile,
                    }
                    for m in _visible_models(provider_id)
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
        models = _visible_models(provider_id)
        if provider_id == "opencode" and not all_models and profile is None:
            typer.echo("  profile: go (use --all to inspect non-Go catalog entries)")

        offered: list[str] | None = None
        if live:
            discover = getattr(registry.get(provider_id), "discover_models", None)
            offered = discover() if callable(discover) else None
            if offered is None:
                typer.echo("  (live check unavailable for this provider)")

        for model in models:
            details = []
            if model.tier:
                details.append(model.tier)
            if model.id == entry.default_model:
                details.append("default")
            if model.best_for:
                details.append("best for: " + ", ".join(model.best_for))
            if model.roles:
                details.append("roles: " + ", ".join(model.roles))
            if model.billing_profile:
                details.append("billing: " + model.billing_profile)
            if model.num_ctx:
                details.append(f"context: {model.num_ctx}")
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


def _active_label(item) -> str:
    return _target_label(item.active_index)


def _target_label(active_index: int) -> str:
    if active_index == 0:
        return "primary"
    return f"fallback {active_index}"
