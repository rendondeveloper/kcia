"""Manage tasks and work items."""

from __future__ import annotations

import json
from typing import Optional

import typer

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
) -> None:
    repo = find_repo_root()
    if repo is None:
        typer.echo("No git repository found. Initialize git or run from a repo root.")
        raise typer.Exit(code=1)

    scope_paths = scope or []
    if scope_paths:
        _validate_scope(repo, scope_paths)

    manifest_raw = load_manifest_raw(repo)
    mode = classify_input(text, manifest_raw, ticket=ticket, prompt=prompt)
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
