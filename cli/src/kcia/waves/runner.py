"""Wave execution."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

from kcia.config import resolve_agents
from kcia.providers.base import RunRequest
from kcia.providers.catalog import load_catalog
from kcia.providers.registry import get_adapter
from kcia.providers.runner import run_provider
from kcia.waves.definitions import WaveDefinition, get_wave, load_waves
from kcia.waves.prompts import build_prompt
from kcia.waves.session import Session, load_manifest, runs_dir
from kcia.waves.validation import build_validation_plan, run_validation


@dataclass
class WaveResult:
    wave_id: str
    status: str
    output_path: str | None = None
    error: str | None = None
    prompt_path: str | None = None


ProviderRunner = Callable[..., object]


def next_pending_wave(session: Session) -> WaveDefinition | None:
    for wave in load_waves():
        if session.wave_status(wave.id) == "pending":
            return wave
    return None


def check_requires(session: Session, wave: WaveDefinition, *, force: bool = False) -> None:
    if force:
        return
    missing = [
        required
        for required in wave.requires
        if session.wave_status(required) != "completed"
    ]
    if missing:
        raise RuntimeError(
            f"wave '{wave.id}' requires completed waves: {', '.join(missing)}"
        )


def run_wave(
    wave_id: str,
    session: Session,
    *,
    force: bool = False,
    validation_error: str | None = None,
    provider_runner: ProviderRunner | None = None,
) -> WaveResult:
    wave = get_wave(wave_id)
    check_requires(session, wave, force=force)
    session.clear_stale_lock()
    session.acquire_lock()

    started_at = _now_iso()
    attempts = int(session.waves.get(wave_id, {}).get("attempts", 0)) + 1
    session.set_wave_status(wave_id, "running", started_at=started_at, attempts=attempts)
    session.save()

    prompt_path: Path | None = None
    try:
        resolved_agents = resolve_agents(session.repo_root)
        agent = resolved_agents[wave.agent]
        catalog = load_catalog()
        if agent.provider not in catalog:
            raise RuntimeError(f"unknown provider '{agent.provider}'")

        adapter = get_adapter(agent.provider)
        executable = adapter.locate()
        if executable is None:
            entry = catalog[agent.provider]
            raise RuntimeError(
                f"provider '{agent.provider}' is not installed. {entry.install_hint}"
            )

        prompt = build_prompt(wave, session, validation_error=validation_error)
        prompt_path = _write_prompt_file(session, wave_id, attempts, prompt)

        req = RunRequest(
            prompt=prompt,
            model=agent.model,
            allow_edits=wave.allow_edits,
            stream=adapter.capabilities.supports_streaming,
            workspace_dirs=[session.repo_root],
            session_id=None,
            resume=False,
            effort=agent.effort,
            allowed_tools=None,
            disallowed_tools=None,
            cwd=session.repo_root,
        )

        runner = provider_runner or run_provider
        result = runner(adapter, req)  # type: ignore[operator]

        output_path = _write_wave_outputs(wave, session, result.output_text)  # type: ignore[attr-defined]

        if wave.validation == "required":
            manifest = load_manifest(session.repo_root)
            if manifest is None:
                raise RuntimeError("manifest required for validation but missing; run `kcia init`")
            plan = build_validation_plan(
                session,
                manifest,
                touched=[session.repo_root],
                repo_root=session.repo_root,
            )
            retry_limit = 3
            current_error = validation_error
            for _ in range(retry_limit):
                report = run_validation(plan, retry_limit=1)
                if report.success:
                    break
                current_error = _format_validation_failures(report)
                failed_profiles = {failure.step.profile_id for failure in report.failures}
                if wave.id != "implementation":
                    raise RuntimeError(current_error)
                retry_prompt = build_prompt(
                    wave,
                    session,
                    validation_error=current_error,
                )
                prompt_path = _write_prompt_file(session, wave_id, attempts, retry_prompt)
                req = RunRequest(
                    prompt=retry_prompt,
                    model=agent.model,
                    allow_edits=wave.allow_edits,
                    stream=adapter.capabilities.supports_streaming,
                    workspace_dirs=[session.repo_root],
                    session_id=None,
                    resume=False,
                    effort=agent.effort,
                    allowed_tools=None,
                    disallowed_tools=None,
                    cwd=session.repo_root,
                )
                result = runner(adapter, req)  # type: ignore[operator]
                _write_wave_outputs(wave, session, result.output_text)  # type: ignore[attr-defined]
                plan = build_validation_plan(
                    session,
                    manifest,
                    touched=[session.repo_root],
                    repo_root=session.repo_root,
                )
                plan = [step for step in plan if step.profile_id in failed_profiles]
            else:
                raise RuntimeError(current_error or "validation failed")

        finished_at = _now_iso()
        session.set_wave_status(
            wave_id,
            "completed",
            finished_at=finished_at,
            agent={
                "role": wave.agent,
                "provider": agent.provider,
                "model": agent.model,
            },
            output_path=str(output_path) if output_path else None,
            prompt_path=str(prompt_path),
            tokens=getattr(result, "tokens_used", None),
            tool_calls=getattr(result, "tool_calls", 0),
        )
        session.save()
        return WaveResult(
            wave_id=wave_id,
            status="completed",
            output_path=str(output_path) if output_path else None,
            prompt_path=str(prompt_path),
        )
    except Exception as exc:
        session.set_wave_status(
            wave_id,
            "failed",
            finished_at=_now_iso(),
            error=str(exc),
            prompt_path=str(prompt_path) if prompt_path else None,
        )
        session.save()
        return WaveResult(wave_id=wave_id, status="failed", error=str(exc), prompt_path=str(prompt_path) if prompt_path else None)
    finally:
        session.release_lock()


def run_waves_until(
    session: Session,
    target_wave_id: str | None = None,
    *,
    force: bool = False,
    provider_runner: ProviderRunner | None = None,
) -> list[WaveResult]:
    results: list[WaveResult] = []
    for wave in load_waves():
        if session.wave_status(wave.id) == "completed":
            continue
        if session.wave_status(wave.id) == "skipped":
            continue
        result = run_wave(wave.id, session, force=force, provider_runner=provider_runner)
        results.append(result)
        if result.status != "completed":
            break
        if target_wave_id and wave.id == target_wave_id:
            break
    return results


def _write_prompt_file(session: Session, wave_id: str, attempt: int, prompt: str) -> Path:
    path = runs_dir(session.repo_root) / f"{wave_id}-{attempt:02d}.prompt.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(prompt, encoding="utf-8")
    return path


def _write_wave_outputs(wave: WaveDefinition, session: Session, output_text: str) -> Path | None:
    if not output_text.strip():
        return None
    if not wave.writes:
        return None
    primary = wave.writes[0]
    target = session.repo_root / primary
    target.parent.mkdir(parents=True, exist_ok=True)
    if not wave.allow_edits or not _context_exists(target):
        target.write_text(output_text, encoding="utf-8")
    return target


def _context_exists(path: Path) -> bool:
    return path.is_file() and path.read_text(encoding="utf-8").strip() != ""


def _format_validation_failures(report: object) -> str:
    lines = ["validation failed:"]
    for failure in report.failures:  # type: ignore[attr-defined]
        lines.append(
            f"- profile {failure.step.profile_id} ({failure.step.command_name}): "
            f"exit {failure.exit_code}\n{failure.output}"
        )
    return "\n".join(lines)


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
