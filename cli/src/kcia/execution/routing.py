"""Pure routing policy for primary/fallback selection."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Callable, Literal

from kcia.config import ResolvedAgent
from kcia.execution.models import AgentTarget
from kcia.execution.quota import QuotaSnapshot
from kcia.providers.base import ProviderFailure, ProviderFailureKind

RouteState = Literal[
    "PRIMARY_ACTIVE",
    "FALLBACK_ACTIVE",
    "SWITCH_PENDING",
    "WAITING_FOR_CAPACITY",
    "MANUAL_PIN",
    "STOPPED",
]


@dataclass(frozen=True)
class RoutingDecision:
    state: RouteState
    target: AgentTarget | None
    reason: str
    switched: bool = False
    retry_at: datetime | None = None


@dataclass(frozen=True)
class RoutingPolicy:
    automatic: bool = False
    warn_at_percent: float = 90
    return_to_primary: str = "safe_boundary"
    all_unavailable: str = "wait"
    max_switches_per_operation: int = 3
    allow_paid_overage: bool = False
    unknown_quota_retry_seconds: int = 300
    anti_flap_seconds: int = 120

    def select_initial(
        self,
        route: ResolvedAgent,
        snapshots: dict[str, QuotaSnapshot] | None = None,
        *,
        pinned_index: int | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> RoutingDecision:
        now = _now(clock)
        targets = targets_for_route(route)
        if pinned_index is not None:
            target = targets[pinned_index]
            return RoutingDecision("MANUAL_PIN", target, "manual pin")
        for index, target in enumerate(targets):
            snapshot = (snapshots or {}).get(target.id)
            available = snapshot.is_available(now) if snapshot else None
            if available is False:
                continue
            state: RouteState = "PRIMARY_ACTIVE" if index == 0 else "FALLBACK_ACTIVE"
            return RoutingDecision(state, target, "available or unknown quota")
        return RoutingDecision(
            "WAITING_FOR_CAPACITY",
            None,
            "all configured targets are unavailable",
            retry_at=now + timedelta(seconds=self.unknown_quota_retry_seconds),
        )

    def after_failure(
        self,
        route: ResolvedAgent,
        current: AgentTarget,
        failure: ProviderFailure,
        snapshots: dict[str, QuotaSnapshot] | None = None,
        *,
        switches_so_far: int = 0,
        clock: Callable[[], datetime] | None = None,
    ) -> RoutingDecision:
        now = _now(clock)
        if failure.kind is ProviderFailureKind.RATE_LIMIT and failure.retryable:
            return RoutingDecision(
                "SWITCH_PENDING",
                current,
                "temporary rate limit",
                retry_at=_retry_at(failure, now),
            )
        if failure.kind is not ProviderFailureKind.QUOTA_EXHAUSTED:
            return RoutingDecision("STOPPED", current, failure.kind.value)
        if not self.automatic or switches_so_far >= self.max_switches_per_operation:
            return RoutingDecision("WAITING_FOR_CAPACITY", None, "automatic routing disabled")

        targets = targets_for_route(route)
        for target in targets:
            if target == current:
                continue
            snapshot = (snapshots or {}).get(target.id)
            available = snapshot.is_available(now) if snapshot else None
            if available is False:
                continue
            return RoutingDecision(
                "FALLBACK_ACTIVE" if target != targets[0] else "PRIMARY_ACTIVE",
                target,
                "quota exhausted",
                switched=True,
            )
        return RoutingDecision(
            "WAITING_FOR_CAPACITY",
            None,
            "no eligible fallback",
            retry_at=now + timedelta(seconds=self.unknown_quota_retry_seconds),
        )


def targets_for_route(route: ResolvedAgent) -> tuple[AgentTarget, ...]:
    primary = AgentTarget(
        provider=route.provider,
        model=route.model,
        effort=route.effort,
    )
    fallbacks = tuple(
        AgentTarget(
            provider=item.provider,
            model=item.model,
            effort=item.effort,
            billing_profile=item.billing_profile,
        )
        for item in getattr(route, "fallbacks", ())
    )
    return (primary, *fallbacks)


def _now(clock: Callable[[], datetime] | None) -> datetime:
    return clock() if clock else datetime.now(UTC)


def _retry_at(failure: ProviderFailure, now: datetime) -> datetime:
    if failure.retry_at:
        try:
            return datetime.fromisoformat(failure.retry_at)
        except ValueError:
            pass
    return now
