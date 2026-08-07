"""Run and inspect pipeline waves."""

from __future__ import annotations

import signal
from pathlib import Path
from typing import Optional

import typer

from kcia.config import resolve_agents
from kcia.paths import find_repo_root
from kcia.usage import format_tokens
from kcia.waves.definitions import get_wave, load_waves
from kcia.waves.progress import WaveProgress
from kcia.waves.runner import next_pending_wave, run_wave, run_waves_until
from kcia.waves.session import Session, runs_dir

app = typer.Typer(help="Run and inspect pipeline waves.", no_args_is_help=True)

_cancel_requested = False


def _handle_sigint(signum: int, frame: object) -> None:
    global _cancel_requested
    _cancel_requested = True


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
        typer.echo(line)

    if total:
        typer.echo(f"\ntotal: {format_tokens(total)} tokens")


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
) -> None:
    global _cancel_requested
    _cancel_requested = False
    signal.signal(signal.SIGINT, _handle_sigint)

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

    reporter = _ProgressReporter(enabled=not quiet)

    if wave_id:
        if _cancel_requested:
            typer.echo("Cancelled.")
            raise typer.Exit(code=130)
        result = run_wave(
            wave_id,
            session,
            force=force,
            on_event=reporter.handle,
            on_wave_start=reporter.start,
        )
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
        if _cancel_requested:
            pending = next_pending_wave(session)
            if pending:
                session.set_wave_status(pending.id, "failed", error="interrupted")
                session.save()
            typer.echo("Cancelled.")
            raise typer.Exit(code=130)

        pending = next_pending_wave(session)
        if pending is None:
            typer.echo("All waves completed.")
            return

        result = run_wave(
            pending.id,
            session,
            force=force,
            on_event=reporter.handle,
            on_wave_start=reporter.start,
        )
        reporter.finish(failed=result.status != "completed")
        if result.status != "completed":
            typer.echo(f"Wave `{pending.id}` failed: {result.error}")
            raise typer.Exit(code=1)
        if target and pending.id == target:
            return


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
