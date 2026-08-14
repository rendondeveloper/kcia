"""Record and search a durable, git-tracked history of work sessions."""

from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional

import typer

from kcia.history import index, log
from kcia.paths import find_repo_root

app = typer.Typer(help="Record and search durable session history for this repo.", no_args_is_help=True)


def _repo_root() -> Path:
    return find_repo_root() or Path.cwd()


def _parse_file(raw: str) -> dict[str, str]:
    path, _, kind = raw.partition(":")
    kind = kind or "modified"
    if kind not in log.CHANGE_KINDS:
        typer.echo(f"Invalid change kind `{kind}` in `--file {raw}`. Use one of: {', '.join(log.CHANGE_KINDS)}.")
        raise typer.Exit(code=1)
    return {"path": path, "change": kind}


@app.command("log")
def session_log(
    title: str = typer.Option(..., "--title", help="Short title for this session."),
    summary: str = typer.Option("", "--summary", help="Free-text plan/decision summary."),
    decision: List[str] = typer.Option([], "--decision", help="A decision made; repeatable."),
    file: List[str] = typer.Option(
        [], "--file", help="path:change (created|modified|deleted); repeatable. Derived from git if omitted."
    ),
    commit: Optional[str] = typer.Option(None, "--commit", help="Commit SHA this session produced."),
    task_id: Optional[str] = typer.Option(None, "--task-id", help="Related kcia task id."),
) -> None:
    """Append one entry to `.ai/history/sessions.jsonl` and index it."""
    repo_root = _repo_root()
    files = [_parse_file(item) for item in file] if file else None
    try:
        entry = log.entry_from_git(
            repo_root,
            title=title,
            summary=summary,
            decisions=list(decision),
            files=files,
            commit_sha=commit,
            task_id=task_id,
        )
        log.append_entry(repo_root, entry)
        index.sync(repo_root, entry)
    except OSError as exc:
        typer.echo(f"Could not write session history: {exc}")
        raise typer.Exit(code=1) from exc
    typer.echo(f"Logged session {entry.id}")


@app.command("search")
def session_search(
    query: str = typer.Argument(..., help="Search terms."),
    limit: int = typer.Option(10, "--limit", help="Maximum results."),
    as_json: bool = typer.Option(False, "--json", help="Print full JSON entries."),
) -> None:
    """Search session history (full-text when available, substring match otherwise)."""
    repo_root = _repo_root()
    try:
        hits = index.search(repo_root, query, limit=limit)
    except Exception as exc:  # noqa: BLE001 - any sqlite failure is the same symptom to the user
        typer.echo(f"Search failed: {exc}")
        raise typer.Exit(code=1) from exc
    if not hits:
        typer.echo("No matching sessions.")
        return
    for hit in hits:
        if as_json:
            typer.echo(hit.raw_json)
        else:
            typer.echo(f"{hit.id}\t{hit.timestamp}\t{hit.title}")


@app.command("list")
def session_list(
    limit: int = typer.Option(20, "--limit", help="Maximum results."),
    since: Optional[str] = typer.Option(None, "--since", help="Only entries at/after this ISO timestamp."),
) -> None:
    """List the most recent sessions."""
    repo_root = _repo_root()
    hits = index.list_recent(repo_root, limit=limit, since=since)
    if not hits:
        typer.echo("No sessions logged yet. Run `kcia session log --title ...`.")
        return
    for hit in hits:
        typer.echo(f"{hit.id}\t{hit.timestamp}\t{hit.title}")


@app.command("show")
def session_show(entry_id: str = typer.Argument(..., help="Session id, from `kcia session list`.")) -> None:
    """Print the full JSON entry for one session."""
    repo_root = _repo_root()
    hit = index.get(repo_root, entry_id)
    if hit is None:
        typer.echo(f"No session `{entry_id}`.")
        raise typer.Exit(code=1)
    typer.echo(json.dumps(json.loads(hit.raw_json), indent=2, ensure_ascii=False))


@app.command("reindex")
def session_reindex() -> None:
    """Rebuild the local search index from `.ai/history/sessions.jsonl`."""
    repo_root = _repo_root()
    count = index.reindex(repo_root)
    typer.echo(f"Reindexed {count} session(s).")
