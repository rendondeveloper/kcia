"""Git-flow branching for the active task."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

import typer

from kcia.git.commit import COMMIT_TYPES, infer_commit_type
from kcia.git.flow import (
    BaseBranch,
    branch_name,
    detect_base_branch,
    git_config_path,
    load_flow,
    save_base_branch,
)
from kcia.git.repo import (
    GitError,
    branch_exists,
    create_branch,
    current_branch,
    is_dirty,
    is_git_repo,
)
from kcia.paths import find_repo_root
from kcia.waves.session import Session

app = typer.Typer(help="Git-flow branch helpers.", no_args_is_help=True)


def load_repo() -> Path:
    repo = find_repo_root()
    if repo is None or not is_git_repo(repo):
        typer.echo("No git repository found. Run `git init` or work from a repo root.")
        raise typer.Exit(code=1)
    return repo


def task_context(repo: Path) -> tuple[str | None, str | None]:
    """(ticket key, title) of the active task, if there is one."""
    try:
        session = Session.load(repo)
    except (FileNotFoundError, ValueError):
        return None, None
    task = session.data.get("task")
    if not isinstance(task, dict):
        return None, None
    return task.get("ticket_key"), task.get("title")


def interactive() -> bool:
    """Whether there is a human to answer a question."""
    return sys.stdin.isatty()


def resolve_base(repo: Path, explicit: str | None, *, assume_yes: bool) -> str:
    """Settle on a base branch, asking when the repository does not make it obvious."""
    if explicit:
        if not branch_exists(repo, explicit):
            typer.echo(f"Branch `{explicit}` does not exist in this repository.")
            raise typer.Exit(code=1)
        save_base_branch(repo, explicit)
        return explicit

    detected: BaseBranch = detect_base_branch(repo)
    if detected.certain:
        typer.echo(f"Base branch: {detected.name} ({detected.reason}).")
        return detected.name

    if assume_yes or not interactive():
        typer.echo(
            f"Cannot tell which branch to start from — {detected.reason}. "
            "Pass `kcia branch start --base <branch>`."
        )
        raise typer.Exit(code=1)

    typer.echo(f"Cannot tell which branch to start from — {detected.reason}.")
    typer.echo("")
    candidates = list(detected.candidates) or [current_branch(repo)]
    for index, name in enumerate(candidates, start=1):
        marker = "  (current)" if name == current_branch(repo) else ""
        typer.echo(f"  {index}. {name}{marker}")
    typer.echo("")
    choice = typer.prompt(
        "Start from which branch? (number, or type a branch name)",
        default="1",
    ).strip()
    if choice.isdigit() and 1 <= int(choice) <= len(candidates):
        chosen = candidates[int(choice) - 1]
    else:
        chosen = choice
    if not branch_exists(repo, chosen):
        typer.echo(f"Branch `{chosen}` does not exist in this repository.")
        raise typer.Exit(code=1)
    save_base_branch(repo, chosen)
    typer.echo(f"Remembered `{chosen}` as this repository's base branch.")
    return chosen


@app.command("config")
def branch_config() -> None:
    """Show the branching model `kcia init` recorded, and where to change it."""
    repo = load_repo()
    flow = load_flow(repo)
    if not flow.configured:
        typer.echo("Not configured yet — run `kcia init` to decide the branching model.")
        typer.echo("Until then `kcia wave run` leaves you on the current branch.")
        return
    typer.echo(flow.describe() + ".")
    if flow.main_branch:
        typer.echo(f"  main:    {flow.main_branch}")
    if flow.develop_branch:
        typer.echo(f"  develop: {flow.develop_branch}")
    if flow.base_branch:
        typer.echo(f"  base:    {flow.base_branch}")
    typer.echo("")
    typer.echo(f"Edit {git_config_path(repo)} to change it.")


@app.command("base")
def branch_base() -> None:
    """Show the branch a new task would start from."""
    repo = load_repo()
    try:
        detected = detect_base_branch(repo)
    except GitError as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1) from exc
    typer.echo(f"current: {current_branch(repo)}")
    typer.echo(f"base:    {detected.name}")
    typer.echo(f"reason:  {detected.reason}")
    if not detected.certain:
        typer.echo("")
        typer.echo("Not certain — `kcia branch start` will ask, or pass `--base <branch>`.")


@app.command("start")
def branch_start(
    subject: Optional[str] = typer.Argument(
        None, help="What the branch is for; defaults to the active task's title."
    ),
    commit_type: Optional[str] = typer.Option(
        None, "--type", "-t", help=f"One of: {', '.join(COMMIT_TYPES)}."
    ),
    base: Optional[str] = typer.Option(None, "--base", "-b", help="Branch to start from."),
    name: Optional[str] = typer.Option(None, "--name", help="Use this branch name verbatim."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Do not ask for confirmation."),
) -> None:
    """Create the git-flow branch for the active task."""
    repo = load_repo()
    ticket, title = task_context(repo)

    text = subject or title
    if not text:
        typer.echo("No active task. Pass a subject: `kcia branch start \"add the loader\"`.")
        raise typer.Exit(code=1)

    resolved_type = commit_type or infer_commit_type(text, ["src"])
    if resolved_type not in COMMIT_TYPES:
        typer.echo(f"Unknown type '{resolved_type}'; use one of {', '.join(COMMIT_TYPES)}.")
        raise typer.Exit(code=1)

    try:
        base_branch = resolve_base(repo, base, assume_yes=yes)
        target = name or branch_name(resolved_type, ticket=ticket, subject=text)

        if branch_exists(repo, target):
            typer.echo(f"Branch `{target}` already exists. Check it out, or pass `--name`.")
            raise typer.Exit(code=1)

        typer.echo("")
        typer.echo(f"  {base_branch}  ->  {target}")
        if is_dirty(repo):
            typer.echo("")
            typer.echo("  note: uncommitted changes travel with you onto the new branch.")
        typer.echo("")
        if not yes and not typer.confirm("Create it?", default=True):
            typer.echo("Nothing done.")
            raise typer.Exit(code=1)

        create_branch(repo, target, base=base_branch)
    except GitError as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1) from exc

    typer.echo(f"On branch {target}.")

    try:
        session = Session.load(repo)
    except (FileNotFoundError, ValueError):
        return
    task = session.data.get("task")
    if not isinstance(task, dict):
        return
    task["branch"] = target
    task["base_branch"] = base_branch
    session.save()
