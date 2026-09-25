"""Common provider execution coordinator.

The coordinator owns provider switching for one logical operation. Callers still
own operation-specific prompts, progress display, and result validation.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from kcia.config import ResolvedAgent
from kcia.execution.models import AgentTarget
from kcia.execution.routing import RoutingPolicy, targets_for_route
from kcia.providers.base import (
    ProviderAdapter,
    ProviderFailure,
    ProviderFailureKind,
    RunRequest,
    RunResult,
)
from kcia.providers.events import StreamEvent
from kcia.providers.registry import get_adapter
from kcia.providers.runner import call_provider, run_provider

ProviderRunner = Callable[..., RunResult]
RequestFactory = Callable[[AgentTarget, ProviderAdapter], RunRequest]
EventHandlerFactory = Callable[[AgentTarget], Callable[[StreamEvent], None] | None]
AttemptFinished = Callable[[AgentTarget, RunResult], None]


@dataclass(frozen=True)
class CoordinatedRun:
    result: RunResult
    target: AgentTarget
    attempts: int
    switched: bool = False


class ExecutionCoordinator:
    def __init__(
        self,
        *,
        policy: RoutingPolicy | None = None,
        runner: ProviderRunner | None = None,
        adapter_factory: Callable[[str], ProviderAdapter] | None = None,
    ) -> None:
        self.policy = policy or RoutingPolicy()
        self.runner = runner or run_provider
        self.adapter_factory = adapter_factory or get_adapter

    def run(
        self,
        route: ResolvedAgent,
        make_request: RequestFactory,
        *,
        operation_id: str,
        on_event_for_target: EventHandlerFactory | None = None,
        on_attempt_finished: AttemptFinished | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> CoordinatedRun:
        targets = targets_for_route(route)
        active_index = getattr(route, "active_index", 0) or 0
        current = targets[active_index] if 0 <= active_index < len(targets) else targets[0]
        attempts = 0
        switched = current != targets[0]

        while True:
            adapter = self.adapter_factory(current.provider)
            req = make_request(current, adapter)
            on_event = on_event_for_target(current) if on_event_for_target else None
            result = call_provider(
                self.runner,
                adapter,
                req,
                on_event,
                should_cancel,
            )
            attempts += 1
            if on_attempt_finished:
                on_attempt_finished(current, result)

            failure = result.provider_failure
            if not _can_switch(failure):
                return CoordinatedRun(
                    result=result,
                    target=current,
                    attempts=attempts,
                    switched=switched,
                )

            decision = self.policy.after_failure(
                route,
                current,
                failure,
                switches_so_far=attempts - 1,
            )
            if not decision.switched or decision.target is None:
                return CoordinatedRun(
                    result=result,
                    target=current,
                    attempts=attempts,
                    switched=switched,
                )
            current = decision.target
            switched = True


def _can_switch(failure: ProviderFailure | None) -> bool:
    return failure is not None and failure.kind is ProviderFailureKind.QUOTA_EXHAUSTED
