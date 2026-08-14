"""Run and inspect pipeline waves."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

import typer

from kcia.cancel import interruptible
from kcia.config import resolve_agents
from kcia.git.autobranch import ensure_task_branch
from kcia.paths import find_repo_root
from kcia.usage import format_duration, format_tokens
from kcia.waves.definitions import get_wave, load_waves
from kcia.waves.progress import WaveProgress
from kcia.waves.runner import (
    ApprovalRequired,
    approval_document,
    WaveBlocked,
    WaveCancelled,
    check_agents_ready,
    next_pending_wave,
    run_wave,
    run_waves_until,
)
from kcia.waves.session import Session, runs_dir

app = typer.Typer(help="Run and inspect pipeline waves.", no_args_is_help=True)


@app.command("list")
def wave_list() -> None:
    repo = find_repo_root()
    if repo is None:
        typer.echo("No git repository found.")
        raise typer.Exit(code=1)
    try:
        session = Session.load(repo)
    except FileNotFoundError:
        typer.echo("No active task. Run `kcia task init` first.")
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


def _render_blocked(blocked: WaveBlocked) -> None:
    """A wave asked a question; show it and how to answer."""
    typer.echo("")
    typer.echo(f"Stopped at `{blocked.wave.id}` — the agent cannot proceed.")
    typer.echo("")
    typer.echo(f"  {blocked.reason}")
    typer.echo("")
    if blocked.output_path:
        typer.echo(f"Full response: {blocked.output_path}")
    typer.echo("Answer it, then resume:")
    typer.echo("  kcia task answer \"<your answer>\"")
    typer.echo(f"  kcia wave retry {blocked.wave.id}")


def _render_approval_gate(gate: ApprovalRequired) -> None:
    """Show the plan and tell the user how to proceed."""
    typer.echo("")
    typer.echo(
        f"Paused before `{gate.wave.id}` — the first wave that can change your code."
    )
    if gate.document is not None:
        lines = gate.document.read_text(encoding="utf-8").count("\n") + 1
        typer.echo("")
        typer.echo(f"  Plan: {gate.document}  ({lines} lines)")
        typer.echo("")
        # The prompt is composed when the wave runs, so edits made now are picked up.
        typer.echo("Open it and edit it directly if something is wrong — your changes go")
        typer.echo("into the builder's prompt. Then:")
    else:
        typer.echo("")
        typer.echo(
            f"warning: no `{gate.wave.approval_shows}` was produced, "
            "so there is no plan to review."
        )
        typer.echo("")
    typer.echo("  kcia wave plan               print it here")
    typer.echo("  kcia wave approve            approve and continue")
    typer.echo("  kcia task answer \"...\"       add context, then re-run the planning wave")
    typer.echo("  kcia task abort              stop here")


class _ProgressReporter:
    """Owns the live status line for the wave currently running."""

    def __init__(self, *, enabled: bool) -> None:
        self._enabled = enabled
        self._current: WaveProgress | None = None

    def start(self, wave, agent) -> None:  # noqa: ANN001 - callback signature
        self.finish()
        if not self._enabled:
            typer.echo(f"Wave `{wave.id}` running ({agent.provider}/{agent.model}).")
            return
        self._current = WaveProgress(
            wave.id, wave.agent, agent.provider, agent.model
        )
        self._current.start()

    def handle(self, event) -> None:  # noqa: ANN001 - callback signature
        if self._current is not None:
            self._current.handle(event)

    def note(self, text: str) -> None:
        """Show a message of ours on the live line; safe from a signal handler."""
        if self._current is not None:
            self._current.note(text)

    def finish(self, *, failed: bool = False) -> None:
        if self._current is not None:
            self._current.finish(failed=failed)
            self._current = None


@app.command("run")
def wave_run(
    wave_id: Optional[str] = typer.Argument(None, help="Wave id; default is next pending."),
    until: Optional[str] = typer.Option(None, "--until", help="Run waves until this id."),
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
    session = _load_runnable_session()
    _execute(session, wave_id=wave_id, until=until, force=force, quiet=quiet, yes=yes)


def _load_runnable_session() -> Session:
    repo = find_repo_root()
    if repo is None:
        typer.echo("No git repository found.")
        raise typer.Exit(code=1)
    try:
        session = Session.load(repo)
    except FileNotFoundError:
        typer.echo("No active task. Run `kcia task init` first.")
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
) -> None:
    # A blocked wave is not `pending`, so the loop below would otherwise skip
    # straight past it into the wave that depends on the missing answer.
    stalled = next(
        (wave for wave in load_waves() if session.wave_status(wave.id) == "blocked"), None
    )
    if stalled is not None:
        state = session.waves.get(stalled.id, {})
        typer.echo(f"`{stalled.id}` is waiting for an answer:")
        typer.echo(f"  {state.get('blocked_reason', 'reason not recorded')}")
        typer.echo("Answer it, then resume:")
        typer.echo('  kcia task answer "<your answer>"')
        typer.echo(f"  kcia wave retry {stalled.id}")
        raise typer.Exit(code=2)

    problems = check_agents_ready(session.repo_root)
    if problems:
        typer.echo("Cannot start — the configured agents are not ready:")
        for problem in problems:
            typer.echo(f"  - {problem}")
        typer.echo("Run `kcia doctor` for the full picture.")
        raise typer.Exit(code=1)

    # The branching model was decided at `kcia start`; here it is only applied.
    outcome = ensure_task_branch(session)
    if outcome is not None:
        typer.echo(outcome.message)

    reporter = _ProgressReporter(enabled=not quiet)
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
    typer.echo("It is pending again — `kcia wave run` starts it from the top.")


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
            # Nothing is committed automatically: the run ends with the changes in
            # the worktree and the decision to keep them still with the user.
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


@app.command("approve")
def wave_approve(
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
        typer.echo("Run `kcia wave run` to continue.")
        return

    _execute(session, wave_id=None, until=None, force=False, quiet=quiet, yes=False)


@app.command("plan")
def wave_plan() -> None:
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
def wave_retry(wave_id: Optional[str] = typer.Argument(None)) -> None:
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
    session.set_wave_status(target_id, "pending")
    session.save()
    result = run_wave(target_id, session, force=True)
    if result.status != "completed":
        typer.echo(f"Retry failed: {result.error}")
        raise typer.Exit(code=1)
    typer.echo(f"Wave `{target_id}` completed on retry.")


@app.command("skip")
def wave_skip(
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
def wave_logs(wave_id: str = typer.Argument(...)) -> None:
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
