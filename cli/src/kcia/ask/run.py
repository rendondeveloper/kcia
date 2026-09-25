"""Run one planner turn for `kcia ask`."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from kcia.ask.conversation import AskConversation
from kcia.ask.prompt import build_ask_prompt
from kcia.config import resolve_agents
from kcia.execution.coordinator import ExecutionCoordinator
from kcia.execution.models import AgentTarget
from kcia.execution.routing import RoutingPolicy
from kcia.mcp.config import (
    CURSOR_CONFIG,
    OPENCODE_CONFIG,
    allowed_tools_for_role,
    render_claude_config,
    servers_for_role,
)
from kcia.providers.base import ProviderAdapter, RunRequest, RunResult
from kcia.providers.catalog import load_catalog
from kcia.providers.registry import get_adapter
from kcia.providers.runner import run_provider
from kcia.waves.progress import WaveProgress
from kcia.waves.session import runs_dir

ASK_ROLE = "planner"


def run_ask_turn(
    repo_root: Path,
    question: str,
    conversation: AskConversation,
    *,
    provider_runner: Callable[..., object] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> tuple[str, AskConversation]:
    """Run the planner and return the reply plus updated conversation state."""
    agents = resolve_agents(repo_root)
    planner = agents[ASK_ROLE]
    catalog = load_catalog()
    if planner.provider not in catalog:
        raise RuntimeError(f"unknown provider '{planner.provider}'")

    primary_adapter = get_adapter(planner.provider)
    if primary_adapter.locate() is None:
        entry = catalog[planner.provider]
        raise RuntimeError(
            f"provider '{planner.provider}' is not installed. {entry.install_hint}"
        )

    runner = provider_runner or run_provider
    result: object | None = None
    final_target: AgentTarget | None = None
    last_prompt = ""
    resume_by_target: dict[str, bool] = {}
    progress_by_target: dict[str, WaveProgress] = {}

    def make_request(target: AgentTarget, adapter: ProviderAdapter) -> RunRequest:
        nonlocal last_prompt
        resume = conversation.can_resume(target.provider, target.model)
        prompt = build_ask_prompt(repo_root, question, conversation, resume=resume)
        last_prompt = prompt
        resume_by_target[target.id] = resume
        mcp_config = _render_mcp_config(repo_root, target.provider)
        mcp_tools = allowed_tools_for_role(repo_root, ASK_ROLE) if mcp_config else None
        return RunRequest(
            prompt=prompt,
            model=target.model,
            mcp_config=mcp_config,
            mcp_tools=mcp_tools,
            allow_edits=False,
            stream=adapter.capabilities.supports_streaming,
            workspace_dirs=[repo_root],
            session_id=conversation.provider_session_id if resume else None,
            resume=resume,
            effort=target.effort,
            allowed_tools=None,
            disallowed_tools=None,
            cwd=repo_root,
        )

    def on_event_for_target(target: AgentTarget):
        progress = WaveProgress(
            "ask",
            ASK_ROLE,
            target.provider,
            target.model,
            periodic_updates=True,
        )
        progress_by_target[target.id] = progress
        progress.start()
        return progress.handle

    def on_attempt_finished(target: AgentTarget, attempt_result: RunResult) -> None:
        progress = progress_by_target.pop(target.id, None)
        if progress is None:
            return
        output_text = getattr(attempt_result, "output_text", "") or ""
        progress.finish(
            failed=int(getattr(attempt_result, "exit_code", 0) or 0) != 0
            or not output_text.strip()
        )

    try:
        coordinated = ExecutionCoordinator(
            policy=RoutingPolicy(automatic=planner.automatic),
            runner=runner,
        ).run(
            planner,
            make_request,
            operation_id="ask",
            on_event_for_target=on_event_for_target,
            on_attempt_finished=on_attempt_finished,
            should_cancel=should_cancel,
        )
        result = coordinated.result
        final_target = coordinated.target
    finally:
        for progress in progress_by_target.values():
            progress.finish(failed=True)
        progress_by_target.clear()

    assert result is not None
    run_result = _as_run_result(result)
    output_text = run_result.output_text

    final_resume = resume_by_target.get(final_target.id, False) if final_target else False
    if final_resume and run_result.exit_code != 0:
        conversation.provider_session_id = None
        return run_ask_turn(
            repo_root,
            question,
            conversation,
            provider_runner=provider_runner,
            should_cancel=should_cancel,
        )

    if not output_text.strip():
        raise RuntimeError("planner returned an empty response")

    _write_run_files(repo_root, last_prompt, output_text)
    provider = final_target.provider if final_target else planner.provider
    model = final_target.model if final_target else planner.model
    conversation.append_turns(
        user_text=question,
        planner_text=output_text,
        provider=provider,
        model=model,
        provider_session_id=run_result.session_id,
    )
    conversation.save(repo_root)
    return output_text, conversation


def _render_mcp_config(repo_root: Path, provider: str) -> Path | None:
    entries = servers_for_role(repo_root, ASK_ROLE)
    if not entries:
        return None
    if provider == "claude":
        destination = runs_dir(repo_root) / f"mcp-{ASK_ROLE}.json"
        return render_claude_config(repo_root, ASK_ROLE, destination)
    if provider == "opencode":
        return repo_root / OPENCODE_CONFIG
    return repo_root / CURSOR_CONFIG


def _write_run_files(repo_root: Path, prompt: str, output_text: str) -> None:
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    base = runs_dir(repo_root) / f"ask-{run_id}"
    base.parent.mkdir(parents=True, exist_ok=True)
    base.with_suffix(".prompt.md").write_text(prompt, encoding="utf-8")
    base.with_suffix(".md").write_text(output_text, encoding="utf-8")


def _as_run_result(result: object) -> RunResult:
    if isinstance(result, RunResult):
        return result
    return RunResult(
        output_text=str(getattr(result, "output_text", "") or ""),
        exit_code=int(getattr(result, "exit_code", 0) or 0),
        session_id=getattr(result, "session_id", None),
    )
