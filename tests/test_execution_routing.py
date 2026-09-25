"""Execution routing and quota policy tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from kcia.config import AgentSetting, ResolvedAgent
from kcia.execution.coordinator import ExecutionCoordinator
from kcia.execution.models import AgentTarget, HandoffRecord
from kcia.execution.quota import QuotaSnapshot, QuotaStore, QuotaWindow
from kcia.execution.routing import RoutingPolicy
from kcia.providers.base import (
    ProviderCapabilities,
    ProviderFailure,
    ProviderFailureKind,
    RunRequest,
    RunResult,
)


NOW = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)


def _route() -> ResolvedAgent:
    return ResolvedAgent(
        role="builder",
        provider="cursor",
        model="composer-2.5",
        effort=None,
        origin="global",
        automatic=True,
        fallbacks=(
            AgentSetting(
                provider="opencode",
                model="opencode-go/glm-5.3-flash",
                billing_profile="opencode-go",
            ),
            AgentSetting(
                provider="opencode",
                model="opencode-go/minimax-m3",
                billing_profile="opencode-go",
            ),
        ),
    )


def test_quota_window_warns_and_exhausts_only_with_real_percent() -> None:
    unknown = QuotaWindow("monthly", "model", "month", "unknown")
    warning = QuotaWindow("monthly", "model", "month", "estimated", used_percent=90)
    exhausted = QuotaWindow("weekly", "model", "week", "exact", used_percent=100)

    assert not unknown.should_warn(90)
    assert not unknown.is_exhausted()
    assert warning.should_warn(90)
    assert exhausted.is_exhausted()


def test_quota_store_round_trips_snapshot(tmp_path) -> None:
    store = QuotaStore(tmp_path)
    snapshot = QuotaSnapshot(
        account_key="opencode-go",
        target_id="opencode/opencode-go/glm-5.3-flash",
        observed_at=NOW,
        confidence="estimated",
        expires_at=NOW + timedelta(minutes=5),
        windows=(
            QuotaWindow(
                "monthly",
                "model",
                "month",
                "estimated",
                used_percent=91,
                resets_at=NOW + timedelta(days=1),
                source="local-estimate",
            ),
        ),
    )

    store.save(snapshot)
    loaded = store.load(snapshot.account_key, snapshot.target_id)

    assert loaded == snapshot
    assert loaded.warning_windows(90)[0].source == "local-estimate"


def test_routing_selects_fallback_after_confirmed_quota_failure() -> None:
    route = _route()
    current = AgentTarget(provider="cursor", model="composer-2.5")
    policy = RoutingPolicy(automatic=True)

    decision = policy.after_failure(
        route,
        current,
        ProviderFailure(
            kind=ProviderFailureKind.QUOTA_EXHAUSTED,
            message="quota exhausted",
        ),
        clock=lambda: NOW,
    )

    assert decision.switched
    assert decision.state == "FALLBACK_ACTIVE"
    assert decision.target is not None
    assert decision.target.model == "opencode-go/glm-5.3-flash"


def test_routing_skips_exhausted_fallback_snapshot() -> None:
    route = _route()
    current = AgentTarget(provider="cursor", model="composer-2.5")
    first_fallback_id = "opencode/opencode-go/glm-5.3-flash"
    snapshots = {
        first_fallback_id: QuotaSnapshot(
            account_key="opencode-go",
            target_id=first_fallback_id,
            observed_at=NOW,
            confidence="exact",
            windows=(QuotaWindow("weekly", "model", "week", "exact", used_percent=100),),
        )
    }

    decision = RoutingPolicy(automatic=True).after_failure(
        route,
        current,
        ProviderFailure(
            kind=ProviderFailureKind.QUOTA_EXHAUSTED,
            message="quota exhausted",
        ),
        snapshots,
        clock=lambda: NOW,
    )

    assert decision.target is not None
    assert decision.target.model == "opencode-go/minimax-m3"


def test_routing_does_not_switch_for_non_quota_failure() -> None:
    route = _route()
    current = AgentTarget(provider="cursor", model="composer-2.5")

    decision = RoutingPolicy(automatic=True).after_failure(
        route,
        current,
        ProviderFailure(kind=ProviderFailureKind.AUTH, message="login expired"),
        clock=lambda: NOW,
    )

    assert not decision.switched
    assert decision.state == "STOPPED"
    assert decision.target == current


def test_handoff_prompt_delta_contains_observable_state_only() -> None:
    record = HandoffRecord(
        objective="Fix failing tests",
        criteria=("pytest passes",),
        completed_steps=("Updated catalog",),
        files_changed=("control-plane/providers/catalog.yaml",),
        validations=("pytest tests/test_agents.py",),
        next_step="Run full provider tests",
    )

    text = record.prompt_delta()

    assert "Objective: Fix failing tests" in text
    assert "Updated catalog" in text
    assert "Run full provider tests" in text


def test_coordinator_starts_from_active_fallback_index() -> None:
    route = ResolvedAgent(
        role="builder",
        provider="cursor",
        model="composer-2.5",
        effort=None,
        origin="repo",
        active_index=1,
        manual_pin=True,
        fallbacks=(
            AgentSetting(
                provider="opencode",
                model="opencode-go/glm-5.3-flash",
                billing_profile="opencode-go",
            ),
        ),
    )
    captured_models: list[str] = []

    def adapter_factory(provider: str):
        return SimpleNamespace(
            capabilities=ProviderCapabilities(
                supports_streaming=True,
                supports_sessions=True,
                supports_effort=True,
                supports_tool_restriction=False,
                supports_mcp_config=False,
            )
        )

    def make_request(target, adapter):
        return RunRequest(
            prompt="p",
            model=target.model,
            allow_edits=False,
            stream=True,
            workspace_dirs=[],
            session_id=None,
            resume=False,
            effort=None,
            allowed_tools=None,
            disallowed_tools=None,
            cwd=Path("/tmp"),
        )

    def runner(adapter, req, **kwargs):
        captured_models.append(req.model)
        return RunResult(output_text="ok")

    result = ExecutionCoordinator(
        policy=RoutingPolicy(automatic=True),
        runner=runner,
        adapter_factory=adapter_factory,
    ).run(route, make_request, operation_id="test")

    assert result.target.model == "opencode-go/glm-5.3-flash"
    assert result.switched is True
    assert captured_models == ["opencode-go/glm-5.3-flash"]
