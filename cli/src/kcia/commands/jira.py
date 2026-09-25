"""Jira cycle selection and hand-off to the existing wave runner."""
from __future__ import annotations

import sys
from pathlib import Path

import click
import typer

from kcia.integrations import jira_cycle as jira
from kcia.waves.session import Session, clear_task_context, context_dir, session_path


def select(repo: Path, *, key: str | None = None, subtask: str | None = None,
           profiles: list[str] | None = None, scope: list[str] | None = None,
           auto: bool = False, required_label: str | None = None) -> Session | None:
    cycle = jira.load(repo)
    if session_path(repo).exists():
        raise jira.JiraError("An active task exists. Finish or abort it before starting a Jira task.")
    if cycle and cycle.get("phase") != "complete" and key and cycle["parent"] != key:
        raise jira.JiraError(f"Resume or abort the Jira cycle for {cycle['parent']} first.")
    if not cycle or cycle.get("phase") == "complete":
        if not key:
            return None
        cycle = {"schema_version": 1, "parent": key, "active": None,
                 "delivered": [], "phase": "select", "profiles": profiles or [], "scope": scope or []}
    if cycle.get("active"):
        if cycle["phase"] == "starting":
            subtask = cycle["active"] if cycle["active"] != cycle["parent"] else None
        else:
            raise jira.JiraError("Finish the active subtask with `kcia done` before selecting another.")
    snapshot = jira.fetch(repo, cycle["parent"])
    jira.save(repo, cycle)
    if snapshot.subtasks:
        candidates = jira.pending(snapshot, cycle)
        if required_label:
            candidates = [i for i in candidates if required_label in i.labels]
        if not candidates:
            if all(i.category == "done" for i in snapshot.subtasks):
                jira.transition(repo, cycle["parent"], "parent_finish")
                cycle["phase"] = "complete"
                typer.echo("All Jira subtasks are complete.")
            else:
                typer.echo("All subtasks delivered; waiting for Jira review/completion. Resume with `kcia work`.")
            jira.save(repo, cycle)
            return None
        typer.echo(f"\n{snapshot.parent.key} — {snapshot.parent.summary}")
        for number, item in enumerate(candidates, 1):
            typer.echo(f"  {number}. {item.key} [{item.priority}] {item.summary} ({item.status})")
        if subtask:
            selected = next((i for i in candidates if i.key == subtask), None)
            if selected is None:
                raise jira.JiraError("The requested subtask is not pending under this parent.")
        elif auto:
            selected = candidates[0]
        elif not sys.stdin.isatty():
            typer.echo("Selection saved. Run `kcia work --subtask KEY` to continue without an interactive terminal.")
            return None
        else:
            choice = typer.prompt("Which subtask? (0 to pause)", default=1, type=click.IntRange(0, len(candidates)))
            if choice == 0:
                return None
            selected = candidates[choice - 1]
    else:
        if subtask:
            raise jira.JiraError("This issue has no subtasks.")
        if snapshot.parent.category == "done":
            cycle["phase"] = "complete"
            jira.save(repo, cycle)
            typer.echo("This Jira issue is already complete.")
            return None
        selected = snapshot.parent
    if required_label and required_label not in selected.labels:
        raise jira.JiraError(f"{selected.key} no longer has label {required_label}.")
    if auto:
        cycle["autonomous"] = True
    # Save the choice before remote effects, so a failed transition is resumable.
    cycle.update(active=selected.key, phase="starting")
    jira.save(repo, cycle)
    clear_task_context(repo)
    session = Session.create(repo, text=selected.key, mode="ticket", ticket_key=selected.key,
                             title=selected.summary, active_profiles=cycle["profiles"], scope=cycle["scope"])
    session.data["jira_cycle"] = True
    if auto:
        session.data["jira_autocycle"] = True
    session.save()
    (context_dir(repo) / "ticket.md").write_text(jira.context(snapshot, selected), encoding="utf-8")
    start(repo)
    return session


def start(repo: Path) -> None:
    cycle = jira.load(repo)
    if cycle and cycle["phase"] == "starting":
        jira.transition(repo, cycle["parent"], "start")
        if cycle["active"] != cycle["parent"]:
            jira.transition(repo, cycle["active"], "start")
        cycle["phase"] = "working"
        jira.save(repo, cycle)


def finish(repo: Path, session: Session) -> None:
    cycle = jira.load(repo)
    if cycle and cycle.get("active") is None and session.task["ticket_key"] in cycle.get("delivered", []):
        return
    if not cycle or cycle.get("active") != session.task["ticket_key"]:
        raise jira.JiraError("Jira cycle does not match the active session.")
    event = "merged" if session.data.get("jira_autocycle") else "finish"
    jira.transition(repo, cycle["active"], event)
    if cycle["active"] not in cycle["delivered"]:
        cycle["delivered"].append(cycle["active"])
    cycle.update(active=None, phase="select")
    if cycle["parent"] == session.task["ticket_key"]:
        cycle["phase"] = "complete"
    jira.save(repo, cycle)


def continue_cycle(repo: Path) -> None:
    from kcia.commands.work import _execute

    cycle = jira.load(repo)
    if not cycle or cycle["phase"] == "complete":
        return
    session = select(repo)
    if session:
        _execute(session, wave_id=None, until=None, force=False, quiet=False, yes=False)
