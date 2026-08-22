"""Start tasks and run the wave pipeline."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

import click
import typer
from typer.core import TyperGroup

from kcia.cancel import interruptible
from kcia.config import resolve_agents
from kcia.git.autobranch import ensure_task_branch
from kcia.git.cycle import is_cycle_open
from kcia.integrations.tickets import (
    FetchResult,
    atlassian_available,
    fetch_agent,
    fetch_ticket,
)
from kcia.paths import find_repo_root
from kcia.usage import collect_usage, format_duration, format_tokens
from kcia.waves.definitions import get_wave, load_waves
from kcia.waves.progress import WaveProgress
from kcia.waves.runner import (
    ApprovalRequired,
    approval_document,
    WaveBlocked,
    WaveCancelled,
    WaveResult,
    check_agents_ready,
    find_blocked_wave,
    next_pending_wave,
    retry_wave,
    run_wave,
)
from kcia.waves.session import Session, classify_input, load_manifest_raw, runs_dir

_WORK_SUBCOMMANDS = frozenset(
    {
        "show",
        "fetch",
        "answer",
        "inject",
        "abort",
        "list",
        "approve",
        "plan",
        "retry",
        "skip",
        "logs",
    }
)


class WorkGroup(TyperGroup):
    """Route `kcia work list` to the list subcommand, not the bare `text=list` action."""

    @staticmethod
    def _state(ctx: click.Context) -> dict:
        root = ctx
        while root.parent is not None:
            root = root.parent
        root.ensure_object(dict)
        return root.obj

    def resolve_command(
        self, ctx: click.Context, args: list[str]
    ) -> tuple[str | None, click.Command | None, list[str]]:
        if args and args[0] in _WORK_SUBCOMMANDS:
            return super().resolve_command(ctx, args)

        state = self._state(ctx)
        if state.get("_work_routed"):
            text = state.get("work_text")
            if text:
                text_args = text.split()
                if args[: len(text_args)] == text_args:
                    args = args[len(text_args) :]
            if not args:
                args = list(state.get("_work_option_args") or [])
            if not args:
                return None, self, []
            return super().resolve_command(ctx, args)

        if args and not args[0].startswith("-"):
            text_parts: list[str] = []
            index = 0
            while index < len(args) and not args[index].startswith("-"):
                text_parts.append(args[index])
                index += 1
            state["work_text"] = " ".join(text_parts)
            state["_work_option_args"] = args[index:]
            args = args[index:]
            ctx._protected_args = []
        else:
            state["work_text"] = None
            state["_work_option_args"] = list(args)

        state["_work_routed"] = True
        return None, self, args


app = typer.Typer(
    help="Start tasks and run the wave pipeline.",
    invoke_without_command=True,
    cls=WorkGroup,
)


def _validate_scope(repo: Path, scope: list[str]) -> None:
    for item in scope:
        target = repo / item
        if not target.exists():
            typer.echo(
                f"Scope path does not exist: {item!r} (resolved to {target}). "
                "Use a path relative to the repository root."
            )
            raise typer.Exit(code=1)


def _create_task(
    repo: Path,
    text: str,
    *,
    ticket: bool,
    prompt: bool,
    profile: Optional[list[str]],
    scope: Optional[list[str]],
    fetch: Optional[bool],
) -> Session:
    scope_paths = scope or []
    if scope_paths:
        _validate_scope(repo, scope_paths)

    if is_cycle_open(repo):
        from kcia.git.cycle import load_cycle

        cycle = load_cycle(repo)
        if cycle is not None:
            typer.echo(
                f"Open work cycle on `{cycle.branch}` — this task continues on the same "
                "branch until `kcia done`."
            )

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
    return session


def _fetch_with_progress(repo: Path, ticket_key: str) -> FetchResult:
    """Run the fetch behind the same live status line the waves use."""
    agent = fetch_agent(repo)
    progress = WaveProgress(
        f"fetch {ticket_key}",
        "planner",
        agent.provider,
        agent.model,
        periodic_updates=True,
    )
    result = FetchResult(error="interrupted")
    progress.start()
    try:
        with interruptible(on_request=lambda: progress.note("stopping…")) as cancel:
            result = fetch_ticket(
                repo, ticket_key, on_event=progress.handle, should_cancel=cancel
            )
    finally:
        progress.finish(failed=not result.ok)
    return result


def _fetch_ticket(repo: Path, ticket_key: str, *, fetch: bool | None) -> None:
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


def report_retry_result(target_id: str, result: WaveResult) -> None:
    if result.status != "completed":
        typer.echo(f"Retry failed: {result.error}")
        raise typer.Exit(code=1)
    typer.echo(f"Wave `{target_id}` completed on retry.")


def _render_blocked(blocked: WaveBlocked) -> None:
    typer.echo("")
    typer.echo(f"Stopped at `{blocked.wave.id}` — the agent cannot proceed.")
    typer.echo("")
    typer.echo(f"  {blocked.reason}")
    typer.echo("")
    if blocked.output_path:
        typer.echo(f"Full response: {blocked.output_path}")
    typer.echo("Answer it, then resume:")
    typer.echo('  kcia work answer "<your answer>"')


def _render_approval_gate(gate: ApprovalRequired) -> None:
    typer.echo("")
    typer.echo(
        f"Paused before `{gate.wave.id}` — the first wave that can change your code."
    )
    if gate.document is not None:
        lines = gate.document.read_text(encoding="utf-8").count("\n") + 1
        typer.echo("")
        typer.echo(f"  Plan: {gate.document}  ({lines} lines)")
        typer.echo("")
        typer.echo("Open it and edit it directly if something is wrong — your changes go")
        typer.echo("into the builder's prompt. Then:")
    else:
        typer.echo("")
        typer.echo(
            f"warning: no `{gate.wave.approval_shows}` was produced, "
            "so there is no plan to review."
        )
        typer.echo("")
    typer.echo("  kcia work plan               print it here")
    typer.echo("  kcia work approve            approve and continue")
    typer.echo('  kcia work answer "..."       add context, then re-run the planning wave')
    typer.echo("  kcia work abort              stop here")


class _ProgressReporter:
    """Owns the live status line for the wave currently running."""

    def __init__(self, *, enabled: bool, periodic_updates: bool = True) -> None:
        self._enabled = enabled
        self._periodic_updates = periodic_updates
        self._current: WaveProgress | None = None

    def start(self, wave, agent) -> None:  # noqa: ANN001 - callback signature
        self.finish()
        if not self._enabled:
            typer.echo(f"Wave `{wave.id}` running ({agent.provider}/{agent.model}).")
            return
        self._current = WaveProgress(
            wave.id,
            wave.agent,
            agent.provider,
            agent.model,
            periodic_updates=self._periodic_updates,
        )
        self._current.start()

    def handle(self, event) -> None:  # noqa: ANN001 - callback signature
        if self._current is not None:
            self._current.handle(event)

    def note(self, text: str) -> None:
        if self._current is not None:
            self._current.note(text)

    def finish(self, *, failed: bool = False) -> None:
        if self._current is not None:
            self._current.finish(failed=failed)
            self._current = None


def _load_runnable_session() -> Session:
    repo = find_repo_root()
    if repo is None:
        typer.echo("No git repository found.")
        raise typer.Exit(code=1)
    try:
        session = Session.load(repo)
    except FileNotFoundError:
        typer.echo('No active task. Run `kcia work "<what you want>"` first.')
        raise typer.Exit(code=1)

    if session.is_locked():
        lock = session.data.get("lock", {})
        typer.echo(
            f"Another wave is running (pid {lock.get('pid')}, acquired {lock.get('acquired_at')})."
        )
        raise typer.Exit(code=1)
    return session


def _execute(
    session: Session,
    *,
    wave_id: Optional[str],
    until: Optional[str],
    force: bool,
    quiet: bool,
    yes: bool,
    periodic_updates: bool = True,
) -> None:
    stalled = next(
        (wave for wave in load_waves() if session.wave_status(wave.id) == "blocked"), None
    )
    if stalled is not None:
        state = session.waves.get(stalled.id, {})
        typer.echo(f"`{stalled.id}` is waiting for an answer:")
        typer.echo(f"  {state.get('blocked_reason', 'reason not recorded')}")
        typer.echo("Answer it, then resume:")
        typer.echo('  kcia work answer "<your answer>"')
        raise typer.Exit(code=2)

    problems = check_agents_ready(session.repo_root)
    if problems:
        typer.echo("Cannot start — the configured agents are not ready:")
        for problem in problems:
            typer.echo(f"  - {problem}")
        typer.echo("Run `kcia doctor` for the full picture.")
        raise typer.Exit(code=1)

    outcome = ensure_task_branch(session)
    if outcome is not None:
        typer.echo(outcome.message)

    reporter = _ProgressReporter(
        enabled=not quiet, periodic_updates=periodic_updates
    )
    run_started = time.monotonic()

    with interruptible(on_request=lambda: reporter.note("stopping…")) as cancel:
        _run_loop(
            session,
            wave_id=wave_id,
            until=until,
            force=force,
            yes=yes,
            reporter=reporter,
            cancel=cancel,
            run_started=run_started,
        )


def _render_cancelled(wave_id: str) -> None:
    typer.echo("")
    typer.echo(f"Stopped `{wave_id}`. The provider was terminated and nothing was written.")
    typer.echo("It is pending again — `kcia work` starts it from the top.")


def _retry_with_progress(session: Session, wave_id: str) -> None:
    """Re-run a wave with the same live status line as `kcia work`."""
    reporter = _ProgressReporter(enabled=True, periodic_updates=True)
    with interruptible(on_request=lambda: reporter.note("stopping…")) as cancel:
        try:
            result = retry_wave(
                session,
                wave_id,
                on_event=reporter.handle,
                on_wave_start=reporter.start,
                should_cancel=cancel,
            )
        except ApprovalRequired as gate:
            reporter.finish()
            _render_approval_gate(gate)
            raise typer.Exit(code=2) from gate
        except WaveBlocked as blocked:
            reporter.finish()
            _render_blocked(blocked)
            raise typer.Exit(code=2) from blocked
        except WaveCancelled as stopped:
            reporter.finish(failed=True)
            _render_cancelled(stopped.wave.id)
            raise typer.Exit(code=130) from stopped
        reporter.finish(failed=result.status != "completed")
        report_retry_result(wave_id, result)


def _run_loop(
    session: Session,
    *,
    wave_id: Optional[str],
    until: Optional[str],
    force: bool,
    yes: bool,
    reporter: "_ProgressReporter",
    cancel,  # noqa: ANN001 - kcia.cancel.Cancellation
    run_started: float,
) -> None:
    if wave_id:
        try:
            result = run_wave(
                wave_id,
                session,
                force=force,
                on_event=reporter.handle,
                on_wave_start=reporter.start,
                skip_approval=yes,
                should_cancel=cancel,
            )
        except ApprovalRequired as gate:
            reporter.finish()
            _render_approval_gate(gate)
            raise typer.Exit(code=2) from gate
        except WaveBlocked as blocked:
            reporter.finish()
            _render_blocked(blocked)
            raise typer.Exit(code=2) from blocked
        except WaveCancelled as stopped:
            reporter.finish(failed=True)
            _render_cancelled(stopped.wave.id)
            raise typer.Exit(code=130) from stopped
        reporter.finish(failed=result.status != "completed")
        if result.status == "completed":
            if result.output_path:
                typer.echo(f"Wrote {result.output_path}")
        else:
            typer.echo(f"Wave `{wave_id}` failed: {result.error}")
            raise typer.Exit(code=1)
        return

    target = until
    while True:
        if cancel.requested:
            typer.echo("Cancelled.")
            raise typer.Exit(code=130)

        pending = next_pending_wave(session)
        if pending is None:
            typer.echo(f"All waves completed in {format_duration(time.monotonic() - run_started)}.")
            typer.echo("")
            typer.echo("Review the changes, then close the task:")
            typer.echo("  git diff                     what changed")
            typer.echo("  kcia done                    show the commits, then confirm")
            return

        try:
            result = run_wave(
                pending.id,
                session,
                force=force,
                on_event=reporter.handle,
                on_wave_start=reporter.start,
                skip_approval=yes,
                should_cancel=cancel,
            )
        except ApprovalRequired as gate:
            reporter.finish()
            _render_approval_gate(gate)
            raise typer.Exit(code=2) from gate
        except WaveBlocked as blocked:
            reporter.finish()
            _render_blocked(blocked)
            raise typer.Exit(code=2) from blocked
        except WaveCancelled as stopped:
            reporter.finish(failed=True)
            _render_cancelled(stopped.wave.id)
            raise typer.Exit(code=130) from stopped
        reporter.finish(failed=result.status != "completed")
        if result.status != "completed":
            typer.echo(f"Wave `{pending.id}` failed: {result.error}")
            raise typer.Exit(code=1)
        if target and pending.id == target:
            return


@app.callback(invoke_without_command=True)
def work(
    ctx: typer.Context,
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
    until: Optional[str] = typer.Option(None, "--until", help="Run waves until this id."),
    wave: Optional[str] = typer.Option(
        None, "--wave", help="Run a single wave by id instead of next-pending-and-continue."
    ),
    force: bool = typer.Option(False, "--force", help="Ignore unmet prerequisites."),
    quiet: bool = typer.Option(
        False, "--quiet", "-q", help="Suppress the live progress line."
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Skip the approval gate before waves that change code.",
    ),
) -> None:
    """Create a task and run the pipeline, or continue the active task."""
    if ctx.invoked_subcommand is not None:
        return
    state = WorkGroup._state(ctx)
    if state.get("_work_completed"):
        return

    text: str | None = state.get("work_text")
    pending_options = state.get("_work_option_args") or []
    if text and pending_options:
        if "--scope" in pending_options and not scope:
            return
        if "--profile" in pending_options and not profile:
            return
        if ("--fetch" in pending_options or "--no-fetch" in pending_options) and fetch is None:
            return
        if "--until" in pending_options and until is None:
            return
        if "--wave" in pending_options and wave is None:
            return
        if "--force" in pending_options and not force:
            return
        if ("--quiet" in pending_options or "-q" in pending_options) and not quiet:
            return
        if ("--yes" in pending_options or "-y" in pending_options) and not yes:
            return
        if "--ticket" in pending_options and not ticket:
            return
        if "--prompt" in pending_options and not prompt:
            return

    if text is None:
        session = _load_runnable_session()
        _execute(session, wave_id=wave, until=until, force=force, quiet=quiet, yes=yes)
        state["_work_completed"] = True
        return

    repo = find_repo_root()
    if repo is None:
        typer.echo("No git repository found. Initialize git or run from a repo root.")
        raise typer.Exit(code=1)

    session = _create_task(
        repo,
        text,
        ticket=ticket,
        prompt=prompt,
        profile=profile,
        scope=scope,
        fetch=fetch,
    )
    _execute(session, wave_id=wave, until=until, force=force, quiet=quiet, yes=yes)
    state["_work_completed"] = True


@app.command("show")
def work_show(
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
def work_fetch() -> None:
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
        typer.echo("This task is not a ticket. `work fetch` only applies to ticket mode.")
        raise typer.Exit(code=1)

    result = _fetch_with_progress(repo, ticket_key)
    if not result.ok:
        typer.echo(f"Could not fetch {ticket_key} — {result.error}")
        raise typer.Exit(code=1)
    typer.echo(f"Wrote {result.path}")


@app.command("answer")
@app.command("inject", hidden=True)
def work_answer(
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

    _retry_with_progress(session, blocked.id)


@app.command("abort")
def work_abort() -> None:
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


@app.command("list")
def work_list() -> None:
    repo = find_repo_root()
    if repo is None:
        typer.echo("No git repository found.")
        raise typer.Exit(code=1)
    try:
        session = Session.load(repo)
    except FileNotFoundError:
        typer.echo('No active task. Run `kcia work "<what you want>"` first.')
        raise typer.Exit(code=1)

    agents = resolve_agents(repo)
    total = 0
    for wave in load_waves():
        status = session.wave_status(wave.id)
        agent = agents[wave.agent]
        state = session.waves.get(wave.id, {})
        tokens = state.get("tokens") or 0
        total += tokens
        line = (
            f"{wave.order}. {wave.id}\t{status}\t{wave.agent} "
            f"({agent.provider}/{agent.model})"
        )
        if tokens:
            line += f"\t{format_tokens(tokens)} tokens"
        if wave.requires_approval and status != "completed":
            line += "\t[approved]" if session.is_approved(wave.id) else "\t[needs approval]"
        typer.echo(line)

    if total:
        typer.echo(f"\ntotal: {format_tokens(total)} tokens")


@app.command("approve")
def work_approve(
    note: Optional[str] = typer.Option(None, "--note", help="Reason recorded with the approval."),
    no_run: bool = typer.Option(
        False, "--no-run", help="Record the approval without continuing the run."
    ),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Suppress the live progress line."),
) -> None:
    """Approve the paused wave and continue running."""
    session = _load_runnable_session()

    gated = next(
        (
            wave
            for wave in load_waves()
            if wave.requires_approval
            and session.wave_status(wave.id) != "completed"
            and not session.is_approved(wave.id)
        ),
        None,
    )
    if gated is None:
        typer.echo("Nothing is waiting for approval.")
        raise typer.Exit(code=1)

    session.approve(gated.id, note=note)
    typer.echo(f"Approved `{gated.id}`.")
    if no_run:
        typer.echo("Run `kcia work` to continue.")
        return

    _execute(
        session,
        wave_id=None,
        until=None,
        force=False,
        quiet=quiet,
        yes=False,
        periodic_updates=True,
    )


@app.command("plan")
def work_plan() -> None:
    """Print the plan awaiting approval."""
    repo = find_repo_root()
    if repo is None:
        typer.echo("No git repository found.")
        raise typer.Exit(code=1)
    session = Session.load(repo)
    for wave in load_waves():
        if not wave.requires_approval:
            continue
        document = approval_document(session, wave)
        if document is None:
            typer.echo(f"No `{wave.approval_shows}` yet. Run the planning waves first.")
            raise typer.Exit(code=1)
        typer.echo(document.read_text(encoding="utf-8").rstrip())
        return
    typer.echo("No wave declares an approval document.")
    raise typer.Exit(code=1)


@app.command("retry")
def work_retry(wave_id: Optional[str] = typer.Argument(None)) -> None:
    repo = find_repo_root()
    if repo is None:
        typer.echo("No git repository found.")
        raise typer.Exit(code=1)
    session = Session.load(repo)
    target_id = wave_id
    if target_id is None:
        pending = next_pending_wave(session)
        if pending is None:
            typer.echo("No wave to retry.")
            raise typer.Exit(code=1)
        target_id = pending.id
    _retry_with_progress(session, target_id)


@app.command("skip")
def work_skip(
    wave_id: str = typer.Argument(...),
    reason: str = typer.Option(..., "--reason", help="Why this wave is skipped."),
) -> None:
    repo = find_repo_root()
    if repo is None:
        typer.echo("No git repository found.")
        raise typer.Exit(code=1)
    session = Session.load(repo)
    get_wave(wave_id)
    session.set_wave_status(wave_id, "skipped", reason=reason)
    session.save()
    typer.echo(f"Skipped wave `{wave_id}`.")


@app.command("logs")
def work_logs(wave_id: str = typer.Argument(...)) -> None:
    repo = find_repo_root()
    if repo is None:
        typer.echo("No git repository found.")
        raise typer.Exit(code=1)
    session = Session.load(repo)
    state = session.waves.get(wave_id, {})
    prompt_path = state.get("prompt_path")
    output_path = state.get("output_path")
    if prompt_path and Path(prompt_path).is_file():
        typer.echo(Path(prompt_path).read_text(encoding="utf-8"))
        return
    matches = sorted(runs_dir(repo).glob(f"{wave_id}-*.prompt.md"))
    if matches:
        typer.echo(matches[-1].read_text(encoding="utf-8"))
        return
    if output_path and Path(output_path).is_file():
        typer.echo(Path(output_path).read_text(encoding="utf-8"))
        return
    typer.echo(f"No logs found for wave `{wave_id}`.")
    raise typer.Exit(code=1)
