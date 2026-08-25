"""Ask the planner a direct question without starting a task."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from kcia.ask.conversation import AskConversation, build_work_prompt, clear_conversation
from kcia.ask.run import run_ask_turn
from kcia.cancel import interruptible
from kcia.commands.work import _create_task, _execute, _resolve_input_text
from kcia.paths import find_repo_root
from kcia.waves.runner import check_agents_ready
from kcia.waves.session import Session


def _require_repo() -> Path:
    repo = find_repo_root()
    if repo is None:
        typer.echo("No git repository found.")
        raise typer.Exit(code=1)
    if not (repo / ".ai").is_dir():
        typer.echo("No `.ai/` directory found. Run `kcia init` first.")
        raise typer.Exit(code=1)
    return repo


def _ensure_not_locked(repo: Path) -> None:
    try:
        session = Session.load(repo)
    except FileNotFoundError:
        return
    session.clear_stale_lock()
    if session.is_locked():
        lock = session.data.get("lock", {})
        typer.echo(
            f"Another wave is running (pid {lock.get('pid')}, acquired {lock.get('acquired_at')})."
        )
        raise typer.Exit(code=1)


def _reject_text_with_flag(flag: str) -> None:
    typer.echo(f"Pass only {flag}, or a question — not both.")
    raise typer.Exit(code=1)


def _start_work_from_conversation(repo: Path, conversation: AskConversation) -> None:
    try:
        Session.load(repo)
    except FileNotFoundError:
        pass
    else:
        typer.echo(
            "An active task is already open. Run `kcia work abort` first, then "
            "`kcia ask --work`, or use `kcia work answer` to inject context into the "
            "current task."
        )
        raise typer.Exit(code=1)

    if not conversation.turns:
        typer.echo("No ask conversation yet. Run `kcia ask \"<question>\"` first.")
        raise typer.Exit(code=1)

    text = build_work_prompt(conversation)
    session = _create_task(
        repo,
        text,
        ticket=False,
        prompt=True,
        profile=None,
        scope=None,
        fetch=None,
    )
    _execute(session, wave_id=None, until=None, force=False, quiet=False, yes=False)


def ask(
    text: Optional[list[str]] = typer.Argument(
        None, help="Question for the planner. Prefer --file or --stdin for long text."
    ),
    file: Optional[Path] = typer.Option(
        None,
        "--file",
        "-f",
        help="Read the question from a UTF-8 file.",
    ),
    from_stdin: bool = typer.Option(
        False,
        "--stdin",
        help="Read the question from stdin.",
    ),
    clear: bool = typer.Option(
        False,
        "--clear",
        help="Clear the ask conversation without calling the planner.",
    ),
    work: bool = typer.Option(
        False,
        "--work",
        help="Start `kcia work` from the current ask conversation.",
    ),
) -> None:
    """Ask the planner about this repository without creating a task."""
    repo = _require_repo()
    _ensure_not_locked(repo)

    has_text_source = bool(text) or file is not None or from_stdin
    if clear and (work or has_text_source):
        _reject_text_with_flag("--clear")
    if work and has_text_source:
        _reject_text_with_flag("--work")

    if clear:
        clear_conversation(repo)
        typer.echo("Conversation cleared.")
        return

    conversation = AskConversation.load(repo)

    if work:
        _start_work_from_conversation(repo, conversation)
        return

    positional = " ".join(text) if text else None
    question = _resolve_input_text(positional, file, from_stdin, required=True)
    assert question is not None

    problems = check_agents_ready(repo)
    if problems:
        typer.echo("Cannot ask — the configured planner is not ready:")
        for problem in problems:
            typer.echo(f"  - {problem}")
        typer.echo("Run `kcia doctor` for the full picture.")
        raise typer.Exit(code=1)

    with interruptible() as cancel:
        try:
            reply, _updated = run_ask_turn(
                repo,
                question,
                conversation,
                should_cancel=cancel,
            )
        except RuntimeError as exc:
            typer.echo(str(exc))
            raise typer.Exit(code=1) from exc

    typer.echo(reply)
