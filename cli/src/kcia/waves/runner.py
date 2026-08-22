"""Wave execution."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import threading
from typing import Callable

from kcia.config import ResolvedAgent, resolve_agents
from kcia.providers.base import RunRequest
from kcia.providers.catalog import load_catalog
from kcia.providers.events import StreamEvent
from kcia.providers.registry import get_adapter
from kcia.providers.runner import call_provider, run_provider
from kcia.mcp.config import (
    CURSOR_CONFIG,
    OPENCODE_CONFIG,
    allowed_tools_for_role,
    render_claude_config,
    servers_for_role,
)
from kcia.waves.blocked import detect_blocked
from kcia.waves.definitions import WaveDefinition, get_wave, load_waves
from kcia.waves.prompts import build_prompt, build_prompt_with_stats
from kcia.waves.session import Session, context_dir, load_manifest, runs_dir
from kcia.waves.validation import (
    build_validation_plan,
    empty_suite_retry_message,
    matches_empty_suite,
    run_validation,
)
from kcia.waves.plan_execution import (
    ExecutionBlockError,
    ProfileExecution,
    execution_batches,
    parse_execution_block,
    parse_integration_checklist,
    validate_disjoint_roots,
    validate_execution_against_manifest,
    validate_execution_dependencies,
)


_MULTI_PROFILE_WAVES = {"implementation", "documentation-final"}


@dataclass
class WaveResult:
    wave_id: str
    status: str
    output_path: str | None = None
    error: str | None = None
    prompt_path: str | None = None


ProviderRunner = Callable[..., object]


class WaveBlocked(Exception):
    """Raised when a wave reports it cannot proceed without an answer.

    Distinct from a failure: the work done so far is kept, and the wave is
    resumed with `kcia work answer`.
    """

    def __init__(
        self,
        wave: WaveDefinition,
        reason: str,
        output_path: Path | None,
        *,
        profile_id: str | None = None,
    ) -> None:
        self.wave = wave
        self.reason = reason
        self.output_path = output_path
        self.profile_id = profile_id
        super().__init__(f"wave '{wave.id}' is blocked: {reason}")


class WaveCancelled(Exception):
    """Raised when the user interrupts a running wave.

    Not a failure: the provider is stopped, nothing is written, and the wave goes
    back to `pending` so `kcia work` picks it up again from the start.
    """

    def __init__(self, wave: WaveDefinition) -> None:
        self.wave = wave
        super().__init__(f"wave '{wave.id}' was cancelled")


class ApprovalRequired(Exception):
    """Raised instead of running a wave that a human has not approved yet.

    Not a failure: the wave stays pending so `kcia work` resumes it once
    `kcia work approve` records the decision.
    """

    def __init__(self, wave: WaveDefinition, document: Path | None) -> None:
        self.wave = wave
        self.document = document
        super().__init__(f"wave '{wave.id}' requires approval")


@dataclass
class _Usage:
    """Token and tool-call totals across every provider call made by one wave."""

    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    tool_calls: int = 0
    calls: int = 0

    def add(self, result: object) -> None:
        self.input_tokens += int(getattr(result, "input_tokens", 0) or 0)
        self.output_tokens += int(getattr(result, "output_tokens", 0) or 0)
        self.cached_tokens += int(getattr(result, "cached_tokens", 0) or 0)
        self.tool_calls += int(getattr(result, "tool_calls", 0) or 0)
        self.calls += 1

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens


def next_pending_wave(session: Session) -> WaveDefinition | None:
    for wave in load_waves():
        if session.wave_status(wave.id) == "pending":
            return wave
    return None


def find_blocked_wave(session: Session) -> WaveDefinition | None:
    return next(
        (wave for wave in load_waves() if session.wave_status(wave.id) == "blocked"),
        None,
    )


def retry_wave(
    session: Session,
    wave_id: str,
    *,
    on_event: Callable[[StreamEvent], None] | None = None,
    on_wave_start: Callable[[WaveDefinition, ResolvedAgent], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> WaveResult:
    session.set_wave_status(wave_id, "pending")
    session.save()
    return run_wave(
        wave_id,
        session,
        force=True,
        on_event=on_event,
        on_wave_start=on_wave_start,
        should_cancel=should_cancel,
    )


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


def check_agents_ready(repo_root: Path | None) -> list[str]:
    """Problems that would stop a run, for every configured role.

    Checked up front because the builder's provider is not exercised until the
    fourth wave: without this, three planner waves burn tokens before a missing
    or logged-out `cursor-agent` surfaces.
    """
    from kcia.providers.base import AuthStatus

    problems: list[str] = []
    catalog = load_catalog()
    agents = resolve_agents(repo_root)
    checked: dict[str, AuthStatus] = {}

    for role, agent in agents.items():
        entry = catalog.get(agent.provider)
        if entry is None:
            problems.append(f"{role}: unknown provider `{agent.provider}`")
            continue

        if agent.provider not in checked:
            adapter = get_adapter(agent.provider)
            checked[agent.provider] = (
                AuthStatus.NOT_INSTALLED if adapter.locate() is None else adapter.check_auth()
            )
        status = checked[agent.provider]

        if status is AuthStatus.NOT_INSTALLED:
            problems.append(
                f"{role} needs `{agent.provider}`, which is not installed. {entry.install_hint}"
            )
        elif status is AuthStatus.NOT_AUTHENTICATED:
            problems.append(
                f"{role} needs `{agent.provider}`, which is not authenticated. {entry.auth_hint}"
            )
    return problems


def approval_document(session: Session, wave: WaveDefinition) -> Path | None:
    """The artifact a human reviews before approving this wave, if it exists."""
    if not wave.approval_shows:
        return None
    path = context_dir(session.repo_root) / wave.approval_shows
    return path if path.is_file() else None


def require_approval(session: Session, wave: WaveDefinition, *, skip: bool = False) -> None:
    if skip or not wave.requires_approval or session.is_approved(wave.id):
        return
    raise ApprovalRequired(wave, approval_document(session, wave))


def run_wave(
    wave_id: str,
    session: Session,
    *,
    force: bool = False,
    validation_error: str | None = None,
    provider_runner: ProviderRunner | None = None,
    on_event: Callable[[StreamEvent], None] | None = None,
    on_wave_start: Callable[[WaveDefinition, ResolvedAgent], None] | None = None,
    skip_approval: bool = False,
    should_cancel: Callable[[], bool] | None = None,
) -> WaveResult:
    wave = get_wave(wave_id)
    check_requires(session, wave, force=force)
    require_approval(session, wave, skip=skip_approval)

    if wave_id in _MULTI_PROFILE_WAVES:
        executions = _parse_plan_execution_or_empty(session)
        if executions:
            return _run_multi_profile_wave(
                wave_id,
                session,
                force=force,
                validation_error=validation_error,
                provider_runner=provider_runner,
                on_wave_start=on_wave_start,
                skip_approval=skip_approval,
                should_cancel=should_cancel,
                on_event=on_event,
            )

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

        prompt, prompt_stats = build_prompt_with_stats(
            wave, session, validation_error=validation_error
        )
        if prompt_stats.dropped_tokens:
            dropped_count = sum(1 for section in prompt_stats.sections if section.dropped)
            print(
                f"warning: dropped {dropped_count} reference(s) to fit the context budget",
                flush=True,
            )
        prompt_path = _write_prompt_file(session, wave_id, attempts, prompt)

        # Rendered per wave, so a role only ever sees the servers it may use.
        mcp_config = _render_mcp_config(session, wave, agent.provider)
        mcp_tools = allowed_tools_for_role(session.repo_root, wave.agent) if mcp_config else None

        req = RunRequest(
            prompt=prompt,
            model=agent.model,
            mcp_config=mcp_config,
            mcp_tools=mcp_tools,
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

        if on_wave_start is not None:
            on_wave_start(wave, agent)

        runner = provider_runner or run_provider
        result = call_provider(runner, adapter, req, on_event, should_cancel)
        _raise_if_cancelled(wave, result)
        # A wave can invoke the provider several times (validation retries); the token
        # counts reported are the total for the wave, not just the last attempt.
        usage = _Usage()
        usage.add(result)

        # Checked before writing: a blocked response is a question, not the
        # artifact this wave produces. Writing it would put "BLOCKED: …" into
        # task.md or plan.md, which every later wave then reads as context.
        reason = detect_blocked(result.output_text)  # type: ignore[attr-defined]
        if reason:
            session.set_wave_status(
                wave_id,
                "blocked",
                finished_at=_now_iso(),
                blocked_reason=reason,
                prompt_path=str(prompt_path) if prompt_path else None,
            )
            session.save()
            raise WaveBlocked(
                wave,
                reason,
                _write_blocked_response(session, wave_id, attempts, result.output_text),  # type: ignore[attr-defined]
            )

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
                retry_prompt, _ = build_prompt_with_stats(
                    wave,
                    session,
                    validation_error=current_error,
                )
                prompt_path = _write_prompt_file(session, wave_id, attempts, retry_prompt)
                req = RunRequest(
                    prompt=retry_prompt,
                    model=agent.model,
                    mcp_config=mcp_config,
                    mcp_tools=mcp_tools,
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
                result = call_provider(runner, adapter, req, on_event, should_cancel)
                _raise_if_cancelled(wave, result)
                usage.add(result)
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
            tokens=usage.total or None,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cached_tokens=usage.cached_tokens,
            tool_calls=usage.tool_calls,
            provider_calls=usage.calls,
        )
        session.save()
        return WaveResult(
            wave_id=wave_id,
            status="completed",
            output_path=str(output_path) if output_path else None,
            prompt_path=str(prompt_path),
        )
    except WaveBlocked:
        # Not a failure: the status and reason were already recorded.
        raise
    except WaveCancelled:
        # The user asked to stop. Reset to `pending` rather than `failed`: nothing
        # was written, so the wave is simply not started yet.
        session.set_wave_status(
            wave_id,
            "pending",
            cancelled_at=_now_iso(),
            prompt_path=str(prompt_path) if prompt_path else None,
        )
        session.save()
        raise
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


def _workspace_dirs_for_profile(repo_root: Path, roots: list[str]) -> list[Path]:
    # We need both:
    # - the profile's source root(s) (where code edits happen)
    # - `.ai/` (so roles can read/edit task-plan context when required)
    #
    # `execution.roots` are expected to be manifest entries: commonly `<dir>/**`.
    # For now we map each `<dir>/**` to its `<dir>`.
    code_workspace: set[Path] = set()
    if not roots:
        code_workspace.add(repo_root)
        return [repo_root, repo_root / ".ai"]

    for root in roots:
        r = root.strip()
        if r in {".", "**"}:
            code_workspace.add(repo_root)
            continue
        if r.endswith("/**"):
            code_workspace.add(repo_root / r[: -len("/**")])
        else:
            # Unknown glob shape: be conservative and include repo_root.
            code_workspace.add(repo_root)

    # Keep `.ai/` last so our default `cwd` points at the code root.
    code_dirs = sorted(code_workspace)
    return [*code_dirs, repo_root / ".ai"]


def _parse_plan_execution_or_empty(session: Session) -> list[ProfileExecution]:
    plan_path = context_dir(session.repo_root) / "plan.md"
    if not plan_path.is_file():
        return []
    plan_text = plan_path.read_text(encoding="utf-8")
    return parse_execution_block(plan_text)


def run_wave_for_profile(
    wave_id: str,
    session: Session,
    profile: ProfileExecution,
    *,
    force: bool,
    provider_runner: ProviderRunner | None,
    validation_error: str | None,
    on_event: Callable[[StreamEvent], None] | None,
    should_cancel: Callable[[], bool] | None,
    save_lock: threading.Lock,
) -> WaveResult:
    """Run a single profile instance of an execution wave.

    This is the same basic flow as `run_wave`, but:
    - prompt composition is restricted to one profile bundle
    - workspace/cwd are restricted to the profile's declared roots
    - statuses are recorded under `session.data["profile_runs"]`
    """
    wave = get_wave(wave_id)

    # Locking: allow parallel profile threads, but prevent re-entrancy for
    # the same `(wave_id, profile_id)` pair.
    with save_lock:
        session.clear_stale_lock()
        session.acquire_lock_for(wave_id=wave_id, profile_id=profile.profile_id)

    started_at = _now_iso()
    with save_lock:
        attempts = (
            int(
                (session.data.get("profile_runs") or {})
                .get(profile.profile_id, {})
                .get("waves", {})
                .get(wave_id, {})
                .get("attempts", 0)
            )
            + 1
        )
        session.set_profile_wave_status(
            profile.profile_id,
            wave_id,
            "running",
            started_at=started_at,
            attempts=attempts,
        )

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

        # Only show the bundle for the profile this thread is editing.
        prompt, _ = build_prompt_with_stats(
            wave,
            session,
            validation_error=validation_error,
            active_profile_ids_override=[profile.profile_id],
        )

        prompt_path = _write_prompt_file(session, f"{wave_id}-{profile.profile_id}", 1, prompt)

        # Rendered per wave: role gates which servers can be used.
        mcp_config = _render_mcp_config(session, wave, agent.provider)
        mcp_tools = allowed_tools_for_role(session.repo_root, wave.agent) if mcp_config else None

        workspace_dirs = _workspace_dirs_for_profile(session.repo_root, profile.roots)
        cwd = workspace_dirs[0]

        req = RunRequest(
            prompt=prompt,
            model=agent.model,
            mcp_config=mcp_config,
            mcp_tools=mcp_tools,
            allow_edits=wave.allow_edits,
            stream=adapter.capabilities.supports_streaming,
            workspace_dirs=workspace_dirs,
            session_id=None,
            resume=False,
            effort=agent.effort,
            allowed_tools=None,
            disallowed_tools=None,
            cwd=cwd,
        )

        runner = provider_runner or run_provider
        # Parallel fan-out currently disables live streaming progress to avoid
        # terminal line collisions.
        result = call_provider(runner, adapter, req, on_event, should_cancel)
        _raise_if_cancelled(wave, result)

        reason = detect_blocked(result.output_text)  # type: ignore[attr-defined]
        if reason:
            with save_lock:
                session.set_profile_wave_status(
                    profile.profile_id,
                    wave_id,
                    "blocked",
                    finished_at=_now_iso(),
                    blocked_reason=reason,
                    prompt_path=str(prompt_path) if prompt_path else None,
                )
                blocked_output = _write_blocked_response(
                    session,
                    f"{wave_id}-{profile.profile_id}",
                    int(
                        (session.data.get("profile_runs") or {})
                        .get(profile.profile_id, {})
                        .get("waves", {})
                        .get(wave_id, {})
                        .get("attempts", 1)
                    ),
                    result.output_text,
                )
                session.save()
            raise WaveBlocked(
                wave, reason, blocked_output, profile_id=profile.profile_id
            )

        output_path = _write_wave_outputs_profile(
            wave=wave,
            session=session,
            profile_id=profile.profile_id,
            output_text=result.output_text,  # type: ignore[attr-defined]
            wave_id=wave_id,
        )

        # Validation (implementation wave) is performed per-profile so failures
        # remain isolated.
        if wave.validation == "required":
            manifest = load_manifest(session.repo_root)
            if manifest is None:
                raise RuntimeError(
                    "manifest required for validation but missing; run `kcia init`"
                )

            plan = build_validation_plan(
                session,
                manifest,
                touched=[session.repo_root],
                repo_root=session.repo_root,
            )
            plan = [step for step in plan if step.profile_id == profile.profile_id]

            retry_limit = 3
            current_error = validation_error
            for _ in range(retry_limit):
                report = run_validation(plan, retry_limit=1)
                if report.success:
                    break

                current_error = _format_validation_failures(report)
                retry_prompt, _ = build_prompt_with_stats(
                    wave,
                    session,
                    validation_error=current_error,
                    active_profile_ids_override=[profile.profile_id],
                )
                with save_lock:
                    prompt_path = _write_prompt_file(
                        session,
                        f"{wave_id}-{profile.profile_id}",
                        attempts,
                        retry_prompt,
                    )

                req = RunRequest(
                    prompt=retry_prompt,
                    model=agent.model,
                    mcp_config=mcp_config,
                    mcp_tools=mcp_tools,
                    allow_edits=wave.allow_edits,
                    stream=adapter.capabilities.supports_streaming,
                    workspace_dirs=workspace_dirs,
                    session_id=None,
                    resume=False,
                    effort=agent.effort,
                    allowed_tools=None,
                    disallowed_tools=None,
                    cwd=cwd,
                )
                result = call_provider(
                    runner, adapter, req, on_event, should_cancel
                )
                _raise_if_cancelled(wave, result)
                output_path = _write_wave_outputs_profile(
                    wave=wave,
                    session=session,
                    profile_id=profile.profile_id,
                    output_text=result.output_text,  # type: ignore[attr-defined]
                    wave_id=wave_id,
                )

                plan = build_validation_plan(
                    session,
                    manifest,
                    touched=[session.repo_root],
                    repo_root=session.repo_root,
                )
                plan = [
                    step
                    for step in plan
                    if step.profile_id == profile.profile_id
                ]
            else:
                raise RuntimeError(current_error or "validation failed")

        with save_lock:
            session.set_profile_wave_status(
                profile.profile_id,
                wave_id,
                "completed",
                finished_at=_now_iso(),
                output_path=str(output_path) if output_path else None,
                prompt_path=str(prompt_path) if prompt_path else None,
            )
            session.save()

        return WaveResult(
            wave_id=wave_id,
            status="completed",
            output_path=str(output_path) if output_path else None,
            prompt_path=str(prompt_path) if prompt_path else None,
        )
    except WaveBlocked:
        raise
    except Exception as exc:
        with save_lock:
            session.set_profile_wave_status(
                profile.profile_id,
                wave_id,
                "failed",
                finished_at=_now_iso(),
                error=str(exc),
                prompt_path=str(prompt_path) if prompt_path else None,
            )
            session.save()
        return WaveResult(
            wave_id=wave_id,
            status="failed",
            error=str(exc),
            prompt_path=str(prompt_path) if prompt_path else None,
        )
    finally:
        with save_lock:
            session.release_lock_for(wave_id=wave_id, profile_id=profile.profile_id)


def _write_wave_outputs_profile(
    *,
    wave: WaveDefinition,
    session: Session,
    profile_id: str,
    output_text: str,
    wave_id: str,
) -> Path | None:
    if not output_text.strip():
        return None
    if not wave.writes:
        return None
    primary = wave.writes[0]
    target = session.repo_root / primary
    if wave_id == "documentation-final":
        # Avoid concurrent writers by using profile-specific files, merged
        # at the end of the parent wave.
        target = target.with_name(f"milestones-{profile_id}.md")
    target.parent.mkdir(parents=True, exist_ok=True)
    if not wave.allow_edits or not _context_exists(target):
        target.write_text(output_text, encoding="utf-8")
    return target


def _merge_documentation_final_milestones(
    session: Session, profile_ids: list[str]
) -> Path | None:
    context = context_dir(session.repo_root)
    main = context / "milestones.md"
    chunks: list[str] = []
    for pid in profile_ids:
        chunk_path = context / f"milestones-{pid}.md"
        if not chunk_path.is_file():
            continue
        chunks.append(f"## Profile: {pid}\n\n{chunk_path.read_text(encoding='utf-8').strip()}\n")
    if not chunks:
        return None
    main.write_text("\n\n".join(chunks) + "\n", encoding="utf-8")
    return main


def _run_integration_check(
    session: Session,
    *,
    provider_runner: ProviderRunner | None,
    on_event: Callable[[StreamEvent], None] | None,
    should_cancel: Callable[[], bool] | None,
) -> Path | None:
    """Run a single cross-profile integration check when plan.md declares one."""
    plan_path = context_dir(session.repo_root) / "plan.md"
    if not plan_path.is_file():
        return None
    plan_text = plan_path.read_text(encoding="utf-8")
    checklist = parse_integration_checklist(plan_text)
    if not checklist:
        return None

    wave = replace(
        get_wave("documentation-final"),
        prompt_template="integration-check.md.j2",
        reference_tags=(),
    )
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

    plan_context = (
        f"{plan_text.strip()}\n\n"
        "## Integration checklist (focus)\n\n"
        f"{checklist}\n"
    )
    prompt, _ = build_prompt_with_stats(
        wave,
        session,
        plan_context_override=plan_context,
    )
    prompt_path = _write_prompt_file(session, "integration-check", 1, prompt)

    mcp_config = _render_mcp_config(session, wave, agent.provider)
    mcp_tools = allowed_tools_for_role(session.repo_root, wave.agent) if mcp_config else None
    repo_root = session.repo_root
    req = RunRequest(
        prompt=prompt,
        model=agent.model,
        mcp_config=mcp_config,
        mcp_tools=mcp_tools,
        allow_edits=wave.allow_edits,
        stream=adapter.capabilities.supports_streaming,
        workspace_dirs=[repo_root / ".ai", repo_root],
        session_id=None,
        resume=False,
        effort=agent.effort,
        allowed_tools=None,
        disallowed_tools=None,
        cwd=repo_root,
    )

    runner = provider_runner or run_provider
    result = call_provider(runner, adapter, req, on_event, should_cancel)
    _raise_if_cancelled(wave, result)

    output_text = getattr(result, "output_text", "") or ""
    if not output_text.strip():
        return None

    target = context_dir(session.repo_root) / "integration-check.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(output_text, encoding="utf-8")
    return target


def _run_multi_profile_wave(
    wave_id: str,
    session: Session,
    *,
    force: bool,
    validation_error: str | None,
    provider_runner: ProviderRunner | None,
    on_wave_start: Callable[[WaveDefinition, ResolvedAgent], None] | None,
    skip_approval: bool,
    should_cancel: Callable[[], bool] | None,
    on_event: Callable[[StreamEvent], None] | None,
) -> WaveResult:
    executions = _parse_plan_execution_or_empty(session)
    if not executions:
        raise RuntimeError(
            f"missing execution block in plan.md for multi-profile wave {wave_id!r}"
        )

    manifest = load_manifest(session.repo_root)
    if manifest is None:
        raise RuntimeError("manifest required for multi-profile execution but missing; run `kcia init`")

    validate_execution_against_manifest(executions, manifest)
    validate_disjoint_roots(executions)
    validate_execution_dependencies(executions)

    batches = execution_batches(executions)

    # Shared lock to serialize session json writes across profile threads.
    save_lock = threading.Lock()
    profile_ids = [e.profile_id for e in executions]
    failed_or_blocked_ids: set[str] = set()

    def _mark_skipped(profile_id: str, reason: str) -> None:
        with save_lock:
            session.set_profile_wave_status(
                profile_id,
                wave_id,
                "skipped",
                finished_at=_now_iso(),
                skip_reason=reason,
            )

    # Run in dependency-ordered batches; profiles within a batch stay parallel.
    blocked: list[WaveBlocked] = []
    results: list[WaveResult] = []
    max_workers = max((len(batch) for batch in batches), default=1)
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        for batch in batches:
            to_run: list[ProfileExecution] = []
            for exec_entry in batch:
                blocked_deps = [
                    dep
                    for dep in exec_entry.depends_on
                    if dep in failed_or_blocked_ids
                ]
                if blocked_deps:
                    reason = (
                        "dependency failed or blocked: "
                        + ", ".join(blocked_deps)
                    )
                    _mark_skipped(exec_entry.profile_id, reason)
                    results.append(
                        WaveResult(
                            wave_id=wave_id,
                            status="skipped",
                            error=reason,
                        )
                    )
                    continue
                to_run.append(exec_entry)

            futures: list[tuple[ProfileExecution, object]] = []
            for exec_entry in to_run:
                futures.append(
                    (
                        exec_entry,
                        pool.submit(
                            run_wave_for_profile,
                            wave_id,
                            session,
                            exec_entry,
                            force=force,
                            provider_runner=provider_runner,
                            validation_error=validation_error,
                            on_event=None,
                            should_cancel=should_cancel,
                            save_lock=save_lock,
                        ),
                    )
                )

            for exec_entry, fut in futures:
                try:
                    result = fut.result()
                    results.append(result)
                    if result.status != "completed":
                        failed_or_blocked_ids.add(exec_entry.profile_id)
                except WaveBlocked as exc:
                    blocked.append(exc)
                    if exc.profile_id:
                        failed_or_blocked_ids.add(exc.profile_id)

    if blocked:
        first = blocked[0]
        # Mark the overall wave as blocked so the CLI stops and asks for input.
        session.set_wave_status(
            wave_id,
            "blocked",
            blocked_reason=first.reason,
            blocked_profile_id=first.profile_id,
        )
        session.save()
        raise first

    any_failed = any(r.status != "completed" for r in results)
    if any_failed:
        # Keep the mixed profile state in `profile_runs`; overall is failed so
        # the CLI exits non-zero and the user can retry.
        session.set_wave_status(
            wave_id,
            "failed",
            error=next((r.error for r in results if r.error), None),
        )
        session.save()
        return WaveResult(
            wave_id=wave_id,
            status="failed",
            error=next((r.error for r in results if r.error), None),
        )

    # Completed: documentation-final needs a merge into the single expected
    # output file.
    output_path: Path | None = None
    if wave_id == "documentation-final":
        output_path = _merge_documentation_final_milestones(session, profile_ids)
        _run_integration_check(
            session,
            provider_runner=provider_runner,
            on_event=on_event,
            should_cancel=should_cancel,
        )

    session.set_wave_status(
        wave_id,
        "completed",
        output_path=str(output_path) if output_path else None,
    )
    session.save()
    return WaveResult(
        wave_id=wave_id,
        status="completed",
        output_path=str(output_path) if output_path else None,
    )


def _raise_if_cancelled(wave: WaveDefinition, result: object) -> None:
    if getattr(result, "cancel_reason", None) == "cancelled by user":
        raise WaveCancelled(wave)


def run_waves_until(
    session: Session,
    target_wave_id: str | None = None,
    *,
    force: bool = False,
    provider_runner: ProviderRunner | None = None,
    on_event: Callable[[StreamEvent], None] | None = None,
    on_wave_start: Callable[[WaveDefinition, ResolvedAgent], None] | None = None,
    skip_approval: bool = False,
) -> list[WaveResult]:
    results: list[WaveResult] = []
    for wave in load_waves():
        if session.wave_status(wave.id) == "completed":
            continue
        if session.wave_status(wave.id) == "skipped":
            continue
        result = run_wave(
            wave.id,
            session,
            force=force,
            provider_runner=provider_runner,
            on_event=on_event,
            on_wave_start=on_wave_start,
            skip_approval=skip_approval,
        )
        results.append(result)
        if result.status != "completed":
            break
        if target_wave_id and wave.id == target_wave_id:
            break
    return results


def _render_mcp_config(session: Session, wave: WaveDefinition, provider: str) -> Path | None:
    """The MCP config this wave's role may use, or None when it has no servers.

    Only Claude Code accepts a per-invocation config, so that is where the role
    gating is enforced. Cursor reads `.cursor/mcp.json` for the whole repository
    and OpenCode reads `opencode.json`; the returned path there only signals that
    servers exist (OpenCode has no per-run MCP flag).
    """
    entries = servers_for_role(session.repo_root, wave.agent)
    if not entries:
        return None
    if provider == "claude":
        destination = runs_dir(session.repo_root) / f"mcp-{wave.agent}.json"
        return render_claude_config(session.repo_root, wave.agent, destination)
    if provider == "opencode":
        return session.repo_root / OPENCODE_CONFIG
    return session.repo_root / CURSOR_CONFIG


def _write_blocked_response(
    session: Session, wave_id: str, attempt: int, output_text: str
) -> Path:
    """Keep the full response next to its prompt, out of the context files."""
    path = runs_dir(session.repo_root) / f"{wave_id}-{attempt:02d}.blocked.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(output_text, encoding="utf-8")
    return path


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
        if matches_empty_suite(failure):
            lines.append(
                f"- profile {failure.step.profile_id} ({failure.step.command_name}): "
                f"{empty_suite_retry_message(failure)}"
            )
            continue
        lines.append(
            f"- profile {failure.step.profile_id} ({failure.step.command_name}): "
            f"exit {failure.exit_code}\n{failure.output}"
        )
    return "\n".join(lines)


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
