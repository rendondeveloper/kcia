"""Manage tasks and work items."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer

from kcia.cancel import interruptible
from kcia.integrations.tickets import (
    FetchResult,
    atlassian_available,
    fetch_agent,
    fetch_ticket,
)
from kcia.paths import find_repo_root
from kcia.usage import collect_usage, format_duration, format_tokens
from kcia.waves.progress import WaveProgress
from kcia.commands.wave import report_retry_result
from kcia.waves.runner import find_blocked_wave, retry_wave
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


def _fetch_with_progress(repo: Path, ticket_key: str) -> FetchResult:
    """Run the fetch behind the same live status line the waves use."""
    agent = fetch_agent(repo)
    progress = WaveProgress(f"fetch {ticket_key}", "planner", agent.provider, agent.model)
    result = FetchResult(error="interrupted")
    progress.start()
    try:
        # Ctrl-C stops the provider instead of leaving the terminal stuck on it.
        with interruptible(on_request=lambda: progress.note("stopping…")) as cancel:
            result = fetch_ticket(
                repo, ticket_key, on_event=progress.handle, should_cancel=cancel
            )
    finally:
        progress.finish(failed=not result.ok)
    return result


def _fetch_ticket(repo: Path, ticket_key: str, *, fetch: bool | None) -> None:
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

    result = _fetch_with_progress(repo, ticket_key)
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

    result = _fetch_with_progress(repo, ticket_key)
    if not result.ok:
        typer.echo(f"Could not fetch {ticket_key} — {result.error}")
        raise typer.Exit(code=1)
    typer.echo(f"Wrote {result.path}")


# `inject` was the original name; it described the mechanism (text is injected
# into the next prompt) rather than what the user is doing, which is answering
# the agent or adding context. Kept as a hidden alias so older docs still work.
@app.command("answer")
@app.command("inject", hidden=True)
def task_answer(
    text: list[str] = typer.Argument(..., help="Answer or extra context."),
    no_retry: bool = typer.Option(
        False,
        "--no-retry",
        help="Record the injection without retrying a blocked wave.",
    ),
) -> None:
    """Answer the agent's question, or add context, for the next wave."""
    repo = find_repo_root()
    if repo is None:
        typer.echo("No git repository found.")
        raise typer.Exit(code=1)
    try:
        session = Session.load(repo)
    except FileNotFoundError as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1) from exc
    session.add_injection(" ".join(text))
    if no_retry:
        typer.echo("Injection recorded.")
        return

    blocked = find_blocked_wave(session)
    if blocked is None:
        typer.echo("No wave is blocked; injection recorded for the next wave that runs.")
        return

    report_retry_result(blocked.id, retry_wave(session, blocked.id))


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
