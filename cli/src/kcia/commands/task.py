"""Manage tasks and work items."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer

from kcia.integrations.tickets import atlassian_available, fetch_ticket
from kcia.paths import find_repo_root
from kcia.usage import collect_usage, format_duration, format_tokens
from kcia.waves.session import Session, classify_input, load_manifest_raw

app = typer.Typer(help="Manage tasks and work items.", no_args_is_help=True)


def _validate_scope(repo: Path, scope: list[str]) -> None:
    for item in scope:
        target = repo / item
        if not target.exists():
            typer.echo(
                f"Scope path does not exist: {item!r} (resolved to {target}). "
                "Use a path relative to the repository root."
            )
            raise typer.Exit(code=1)


@app.command("init")
def task_init(
    text: str = typer.Argument(..., help="Ticket key or free-form prompt."),
    ticket: bool = typer.Option(False, "--ticket", help="Force ticket mode."),
    prompt: bool = typer.Option(False, "--prompt", help="Force prompt mode."),
    profile: Optional[list[str]] = typer.Option(None, "--profile", help="Active profile ids."),
    scope: Optional[list[str]] = typer.Option(
        None, "--scope", help="Limit active profiles to these repo-relative paths."
    ),
    fetch: Optional[bool] = typer.Option(
        None,
        "--fetch/--no-fetch",
        help="Fetch the issue into .ai/context/ticket.md (default: on in ticket mode).",
    ),
) -> None:
    repo = find_repo_root()
    if repo is None:
        typer.echo("No git repository found. Initialize git or run from a repo root.")
        raise typer.Exit(code=1)

    scope_paths = scope or []
    if scope_paths:
        _validate_scope(repo, scope_paths)

    manifest_raw = load_manifest_raw(repo)
    mode = classify_input(
        text,
        manifest_raw,
        ticket=ticket,
        prompt=prompt,
        issue_tracker_connected=atlassian_available(repo),
    )
    ticket_key = text.strip() if mode == "ticket" else None
    session = Session.create(
        repo,
        text=text,
        mode=mode,
        ticket_key=ticket_key,
        active_profiles=profile or [],
        scope=scope_paths,
    )
    typer.echo(f"Task {session.task['id']} initialized in {mode} mode.")

    if mode == "ticket":
        _fetch_ticket(repo, ticket_key or text, fetch=fetch)


def _fetch_ticket(repo: "Path", ticket_key: str, *, fetch: bool | None) -> None:
    """Pull the issue body onto disk so the waves start with the real request.

    Defaults to on, because a ticket task whose body never arrives gives the
    planner only the key — the failure mode this exists to remove. `--no-fetch`
    skips the provider call.
    """
    if fetch is False:
        return
    if fetch is None and not atlassian_available(repo):
        typer.echo(
            "No Atlassian MCP server for the planner, so the issue body was not fetched. "
            "Run `kcia mcp add atlassian`, or write .ai/context/ticket.md yourself."
        )
        return

    typer.echo(f"Fetching {ticket_key}…")
    result = fetch_ticket(repo, ticket_key)
    if result.ok:
        typer.echo(f"Wrote {result.path}")
        return

    typer.echo(f"warning: could not fetch {ticket_key} — {result.error}")
    typer.echo(
        "The task is still created. Paste the issue into .ai/context/ticket.md, "
        "or the waves will only receive the key."
    )


@app.command("show")
def task_show(
    as_json: bool = typer.Option(False, "--json", help="Output session as JSON."),
) -> None:
    repo = find_repo_root()
    if repo is None:
        typer.echo("No git repository found.")
        raise typer.Exit(code=1)
    try:
        session = Session.load(repo)
    except FileNotFoundError as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1) from exc
    if as_json:
        typer.echo(json.dumps(session.data, indent=2))
        return
    task = session.task
    typer.echo(f"id: {task['id']}")
    typer.echo(f"mode: {task['mode']}")
    typer.echo(f"title: {task['title']}")
    scope = task.get("scope") or []
    if scope:
        typer.echo(f"scope: {', '.join(scope)}")

    usage = collect_usage(session.waves)
    if usage.total:
        typer.echo("")
        typer.echo(f"tokens: {format_tokens(usage.total)}")
        typer.echo(f"  input:  {format_tokens(usage.input_tokens)}")
        typer.echo(f"  output: {format_tokens(usage.output_tokens)}")
        if usage.cached_tokens:
            typer.echo(f"  cached: {format_tokens(usage.cached_tokens)} (read from cache)")
        typer.echo(f"tool calls: {usage.tool_calls}")
        typer.echo(f"provider calls: {usage.provider_calls}")
        if usage.total_seconds:
            typer.echo(f"elapsed: {format_duration(usage.total_seconds)}")
        typer.echo("")
        for wave_id, tokens in usage.per_wave.items():
            elapsed = usage.per_wave_seconds.get(wave_id)
            duration = format_duration(elapsed) if elapsed is not None else "-"
            typer.echo(f"  {wave_id:<22}{format_tokens(tokens):>8}  {duration:>8}")


@app.command("fetch")
def task_fetch() -> None:
    """Re-fetch the current task's issue into `.ai/context/ticket.md`."""
    repo = find_repo_root()
    if repo is None:
        typer.echo("No git repository found.")
        raise typer.Exit(code=1)
    try:
        session = Session.load(repo)
    except FileNotFoundError as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1) from exc

    ticket_key = session.task.get("ticket_key")
    if not ticket_key:
        typer.echo("This task is not a ticket. `task fetch` only applies to ticket mode.")
        raise typer.Exit(code=1)

    typer.echo(f"Fetching {ticket_key}…")
    result = fetch_ticket(repo, ticket_key)
    if not result.ok:
        typer.echo(f"Could not fetch {ticket_key} — {result.error}")
        raise typer.Exit(code=1)
    typer.echo(f"Wrote {result.path}")


@app.command("inject")
def task_inject(text: str = typer.Argument(..., help="Answer or extra context.")) -> None:
    repo = find_repo_root()
    if repo is None:
        typer.echo("No git repository found.")
        raise typer.Exit(code=1)
    try:
        session = Session.load(repo)
    except FileNotFoundError as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1) from exc
    session.add_injection(text)
    typer.echo("Injection recorded.")


@app.command("abort")
def task_abort() -> None:
    repo = find_repo_root()
    if repo is None:
        typer.echo("No git repository found.")
        raise typer.Exit(code=1)
    try:
        session = Session.load(repo)
    except FileNotFoundError:
        typer.echo("No active task.")
        raise typer.Exit(code=1)
    session.abort()
    typer.echo("Task aborted.")
