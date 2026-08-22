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
from kcia.git.cycle import close_cycle
from kcia.git.flow import ON_DONE_MERGE, load_flow
from kcia.git.repo import (
    GH_BIN,
    GitError,
    checkout,
    commit as git_commit,
    current_branch,
    delete_local_branch,
    delete_remote_branch,
    fetch,
    gh_available,
    merge_no_ff,
    pull,
    push as git_push,
    remote_branch_exists,
    remotes,
    stage,
    unstage_all,
)
from kcia.waves.progress import StepProgress
from kcia.history import index, log
from kcia.waves.definitions import load_waves
from kcia.waves import plan_metadata
from kcia.waves.session import Session, session_path

MAX_LISTED_PATHS = 12
_NON_ENGLISH_CHARS = frozenset("ñÑ¿¡")


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


def _files_from_planned(commits: list[PlannedCommit]) -> list[dict[str, str]]:
    seen: set[str] = set()
    files: list[dict[str, str]] = []
    for planned in commits:
        for path in planned.paths:
            if path not in seen:
                seen.add(path)
                files.append({"path": path, "change": "modified"})
    return files


def _auto_log_session(
    repo: Path,
    *,
    title: str,
    summary: str,
    decisions: list[str],
    commits: list[PlannedCommit],
    commit_sha: str,
    task_id: str | None,
) -> None:
    checked = [title, summary, *decisions]
    offending = next(
        (text for text in checked if any(char in _NON_ENGLISH_CHARS for char in text)),
        None,
    )
    if offending is not None:
        typer.echo(
            "Session was not saved: session history must be written in English "
            f"(found non-English characters in: {offending!r})."
        )
        return
    try:
        entry = log.entry_from_git(
            repo,
            title=title,
            summary=summary,
            decisions=decisions,
            files=_files_from_planned(commits),
            commit_sha=commit_sha,
            task_id=task_id,
        )
        log.append_entry(repo, entry)
        index.sync(repo, entry)
    except OSError as exc:
        typer.echo(f"Session was not saved: {exc}")
        return
    except Exception as exc:  # noqa: BLE001 - index failure must not undo a successful done
        typer.echo(f"Session was not saved: {exc}")
        return
    typer.echo(f"Session {entry.id} saved.")


def _open_pr(repo: Path, branch: str, title: str, base: str | None) -> None:
    if not gh_available():
        typer.echo(
            "`gh` is not installed, so no pull request was opened. "
            "Install it (https://cli.github.com) and re-run, or open the PR yourself."
        )
        raise typer.Exit(code=1)
    args = [GH_BIN, "pr", "create", "--title", title, "--body", "", "--head", branch]
    if base:
        args += ["--base", base]
    result = subprocess.run(args, cwd=repo, capture_output=True, text=True)
    if result.returncode != 0:
        typer.echo(f"gh pr create failed: {(result.stderr or result.stdout).strip()}")
        raise typer.Exit(code=1)
    url = result.stdout.strip()
    if url:
        typer.echo(url)


def _run_step(label: str, action) -> None:
    typer.echo(label)
    with StepProgress(label):
        action()


def _remote_name(repo: Path) -> str:
    names = remotes(repo)
    if not names:
        typer.echo(
            "No git remote configured. Add one with `git remote add origin <url>`."
        )
        raise typer.Exit(code=1)
    return "origin" if "origin" in names else names[0]


def _push_branch(repo: Path, branch: str, *, remote: str) -> None:
    _run_step(
        f"Pushing `{branch}`",
        lambda: git_push(repo, branch, remote=remote),
    )
    typer.echo(f"Pushed `{branch}`.")


def _finish_current_branch(repo: Path, branch: str) -> None:
    remote = _remote_name(repo)
    _push_branch(repo, branch, remote=remote)


def _finish_gitflow_pr(repo: Path, branch: str, title: str, base: str) -> None:
    if not gh_available():
        typer.echo(
            "`gh` is not installed, so the pull request could not be opened. "
            "Install it (https://cli.github.com) and re-run."
        )
        raise typer.Exit(code=1)
    remote = _remote_name(repo)
    _push_branch(repo, branch, remote=remote)
    typer.echo(f"Opening PR to `{base}`")
    with StepProgress(f"Opening PR to `{base}`"):
        _open_pr(repo, branch, title, base)
    typer.echo(f"Opened PR to `{base}`.")


def _finish_gitflow_merge(repo: Path, branch: str, base: str) -> None:
    if branch == base:
        typer.echo(
            f"Already on `{base}`; nothing to merge. Push this branch or switch "
            "to the task branch first."
        )
        raise typer.Exit(code=1)
    remote = _remote_name(repo)
    _run_step(f"Fetching `{remote}`", lambda: fetch(repo, remote))
    _run_step(f"Checking out `{base}`", lambda: checkout(repo, base))
    _run_step(f"Pulling `{remote}/{base}`", lambda: pull(repo, base, remote=remote))
    _run_step(
        f"Merging `{branch}` into `{base}`",
        lambda: merge_no_ff(repo, branch),
    )
    _run_step(
        f"Pushing `{base}`",
        lambda: git_push(repo, base, remote=remote),
    )
    _run_step(
        f"Deleting `{branch}` locally",
        lambda: delete_local_branch(repo, branch),
    )
    if remote_branch_exists(repo, branch, remote=remote):
        _run_step(
            f"Deleting `{branch}` on `{remote}`",
            lambda: delete_remote_branch(repo, branch, remote=remote),
        )
    typer.echo(f"Merged `{branch}` into `{base}`.")


def _after_commit(
    repo: Path,
    *,
    branch: str,
    title: str,
    session_base: str | None,
) -> None:
    flow = load_flow(repo)
    if flow.uses_gitflow:
        base = session_base or flow.base_branch
        if not base:
            typer.echo("Git flow is on, but no base branch is configured.")
            raise typer.Exit(code=1)
        if flow.on_done == ON_DONE_MERGE:
            _finish_gitflow_merge(repo, branch, base)
            return
        _finish_gitflow_pr(repo, branch, title, base)
        return
    _finish_current_branch(repo, branch)


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
) -> None:
    """Review and write the commits that close the task.

    Nothing is written until you confirm: the commits, their messages and the
    exact files in each are printed first. On success, the work is also appended
    to `.ai/history/sessions.jsonl` automatically.
    """
    repo = load_repo()
    session = _session(repo)
    plan = plan_metadata.load(repo)

    resolved_subject = (
        subject
        or (plan.title if plan else None)
        or (session.task.get("title") if session else None)
    )
    if not resolved_subject:
        typer.echo('No active task. Pass a subject: `kcia done "add the loader"`.')
        raise typer.Exit(code=1)

    resolved_ticket = None if no_ticket else (
        ticket
        or (plan.ticket if plan else None)
        or (session.task.get("ticket_key") if session else None)
    )

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
            plan_type=plan.type if plan else None,
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

    if written:
        task_id = session.task.get("id") if session is not None else None
        _auto_log_session(
            repo,
            title=(plan.title if plan else None) or resolved_subject,
            summary=(plan.summary if plan else None) or "",
            decisions=list(plan.decisions) if plan else [],
            commits=commits,
            commit_sha=written[-1][0],
            task_id=task_id,
        )
        close_cycle(repo)
        session_file = session_path(repo)
        if session_file.is_file():
            session_file.unlink()
        session_base = session.task.get("base_branch") if session is not None else None
        try:
            _after_commit(
                repo,
                branch=branch,
                title=written[-1][1],
                session_base=session_base,
            )
        except GitError as exc:
            typer.echo(str(exc))
            raise typer.Exit(code=1) from exc
