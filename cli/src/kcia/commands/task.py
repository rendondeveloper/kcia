"""Manage tasks and work items."""

from __future__ import annotations

import json
from typing import Optional

import typer

from kcia.paths import find_repo_root
from kcia.waves.session import Session, classify_input, load_manifest_raw

app = typer.Typer(help="Manage tasks and work items.", no_args_is_help=True)


@app.command("init")
def task_init(
    text: str = typer.Argument(..., help="Ticket key or free-form prompt."),
    ticket: bool = typer.Option(False, "--ticket", help="Force ticket mode."),
    prompt: bool = typer.Option(False, "--prompt", help="Force prompt mode."),
    profile: Optional[list[str]] = typer.Option(None, "--profile", help="Active profile ids."),
) -> None:
    repo = find_repo_root()
    if repo is None:
        typer.echo("No git repository found. Initialize git or run from a repo root.")
        raise typer.Exit(code=1)

    manifest_raw = load_manifest_raw(repo)
    mode = classify_input(text, manifest_raw, ticket=ticket, prompt=prompt)
    ticket_key = text.strip() if mode == "ticket" else None
    session = Session.create(
        repo,
        text=text,
        mode=mode,
        ticket_key=ticket_key,
        active_profiles=profile or [],
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
