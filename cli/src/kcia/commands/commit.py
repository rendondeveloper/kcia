"""Close a task: review the commits, confirm, and write them."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional

import typer

from kcia.commands.branch import load_repo
from kcia.git.commit import (
    COMMIT_TYPES,
    NothingToCommit,
    PlannedCommit,
    plan_commits,
)
from kcia.git.repo import (
    GH_BIN,
    GitError,
    commit as git_commit,
    current_branch,
    gh_available,
    remotes,
    push as git_push,
    stage,
    unstage_all,
)
from kcia.waves.definitions import load_waves
from kcia.waves.session import Session

MAX_LISTED_PATHS = 12


def _session(repo: Path) -> Session | None:
    """The active task, or None. A committing user is never blocked by a bad session file."""
    try:
        session = Session.load(repo)
    except (FileNotFoundError, ValueError):
        return None
    return session if isinstance(session.data.get("task"), dict) else None


def _unfinished_waves(session: Session) -> list[str]:
    return [
        wave.id
        for wave in load_waves()
        if session.wave_status(wave.id) not in ("completed", "skipped")
    ]


def _render(commits: list[PlannedCommit], branch: str) -> None:
    typer.echo("")
    typer.echo(f"On branch {branch}:")
    for index, planned in enumerate(commits, start=1):
        typer.echo("")
        typer.echo(f"  Commit {index} ({planned.kind})")
        typer.echo(f"    {planned.message}")
        for path in planned.paths[:MAX_LISTED_PATHS]:
            typer.echo(f"      {path}")
        extra = len(planned.paths) - MAX_LISTED_PATHS
        if extra > 0:
            typer.echo(f"      … and {extra} more file(s)")
    typer.echo("")


def _open_pr(repo: Path, branch: str, title: str, base: str | None) -> None:
    if not gh_available():
        typer.echo(
            "`gh` is not installed, so no pull request was opened. "
            "Install it (https://cli.github.com) or open the PR yourself."
        )
        return
    args = [GH_BIN, "pr", "create", "--title", title, "--body", "", "--head", branch]
    if base:
        args += ["--base", base]
    result = subprocess.run(args, cwd=repo, capture_output=True, text=True)
    if result.returncode != 0:
        typer.echo(f"gh pr create failed: {(result.stderr or result.stdout).strip()}")
        return
    typer.echo(result.stdout.strip())


def commit_command(
    subject: Optional[str] = typer.Argument(
        None, help="Commit subject; defaults to the active task's title."
    ),
    commit_type: Optional[str] = typer.Option(
        None, "--type", "-t", help=f"One of: {', '.join(COMMIT_TYPES)}. Inferred when omitted."
    ),
    ticket: Optional[str] = typer.Option(
        None, "--ticket", help="Issue key to prefix the subject with."
    ),
    no_ticket: bool = typer.Option(
        False, "--no-ticket", help="Commit without an issue key even in ticket mode."
    ),
    single: bool = typer.Option(
        False, "--single", help="One commit for plan and code instead of two."
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show the commits and stop."),
    push: bool = typer.Option(False, "--push", help="Push the branch after committing."),
    pr: bool = typer.Option(False, "--pr", help="Open a pull request with `gh` after pushing."),
) -> None:
    """Review and write the commits that close the task.

    Nothing is written until you confirm: the commits, their messages and the
    exact files in each are printed first.
    """
    repo = load_repo()
    session = _session(repo)

    resolved_subject = subject or (session.task.get("title") if session else None)
    if not resolved_subject:
        typer.echo('No active task. Pass a subject: `kcia commit "add the loader"`.')
        raise typer.Exit(code=1)

    resolved_ticket = None if no_ticket else (ticket or (session.task.get("ticket_key") if session else None))

    if session is not None:
        pending = _unfinished_waves(session)
        if pending:
            typer.echo(f"warning: waves not finished yet — {', '.join(pending)}.")

    try:
        commits = plan_commits(
            repo,
            subject=resolved_subject,
            ticket=resolved_ticket,
            commit_type=commit_type,
            single=single,
        )
    except NothingToCommit as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1) from exc
    except (ValueError, GitError) as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1) from exc

    branch = current_branch(repo)
    _render(commits, branch)

    if dry_run:
        typer.echo("Dry run — nothing was committed.")
        return
    if not yes and not typer.confirm("Commit this?", default=False):
        typer.echo("Nothing was committed.")
        raise typer.Exit(code=1)

    written: list[tuple[str, str]] = []
    try:
        for planned in commits:
            unstage_all(repo)
            stage(repo, planned.paths)
            sha = git_commit(repo, planned.message)
            written.append((sha, planned.message))
            typer.echo(f"[{branch} {sha}] {planned.message}")
    except GitError as exc:
        typer.echo(str(exc))
        if written:
            typer.echo(f"{len(written)} commit(s) were already written; the rest were not.")
        raise typer.Exit(code=1) from exc

    if session is not None:
        session.data.setdefault("commits", []).extend(
            {"sha": sha, "message": message, "branch": branch} for sha, message in written
        )
        session.save()

    if not (push or pr):
        return

    if not remotes(repo):
        typer.echo("No git remote configured, so nothing was pushed. Add one with `git remote add origin <url>`.")
        return
    try:
        git_push(repo, branch)
    except GitError as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1) from exc
    typer.echo(f"Pushed {branch}.")

    if pr:
        base = session.task.get("base_branch") if session else None
        _open_pr(repo, branch, written[-1][1], base)
