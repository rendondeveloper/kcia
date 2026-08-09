"""Token usage aggregation and formatting."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class UsageTotals:
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    tool_calls: int = 0
    provider_calls: int = 0
    per_wave: dict[str, int] = field(default_factory=dict)
    per_wave_seconds: dict[str, float] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def total_seconds(self) -> float:
        return sum(self.per_wave_seconds.values())


def collect_usage(waves: dict[str, dict[str, Any]]) -> UsageTotals:
    """Sum the token counters persisted on each wave of a session."""
    totals = UsageTotals()
    for wave_id, state in waves.items():
        tokens = int(state.get("tokens") or 0)
        # Providers that report no usage (cursor today) would otherwise drop the
        # wave from the report entirely, so key on having run rather than on tokens.
        if tokens or state.get("started_at"):
            totals.per_wave[wave_id] = tokens
        elapsed = _wave_seconds(state)
        if elapsed is not None:
            totals.per_wave_seconds[wave_id] = elapsed
        totals.input_tokens += int(state.get("input_tokens") or 0)
        totals.output_tokens += int(state.get("output_tokens") or 0)
        totals.cached_tokens += int(state.get("cached_tokens") or 0)
        totals.tool_calls += int(state.get("tool_calls") or 0)
        totals.provider_calls += int(state.get("provider_calls") or 0)
    return totals


def _wave_seconds(state: dict[str, Any]) -> float | None:
    started, finished = state.get("started_at"), state.get("finished_at")
    if not started or not finished:
        return None
    try:
        delta = datetime.fromisoformat(finished) - datetime.fromisoformat(started)
    except ValueError:
        return None
    return max(delta.total_seconds(), 0.0)


def format_duration(seconds: float) -> str:
    """Compact wall-clock duration: 8.4 -> 8s, 94 -> 1m34s, 3720 -> 1h02m."""
    total = int(round(seconds))
    if total < 60:
        return f"{total}s"
    if total < 3600:
        return f"{total // 60}m{total % 60:02d}s"
    return f"{total // 3600}h{(total % 3600) // 60:02d}m"


def format_tokens(value: int) -> str:
    """Compact, readable token count: 1234 -> 1.2k, 1200000 -> 1.2M."""
    if value < 1_000:
        return str(value)
    if value < 1_000_000:
        return f"{value / 1_000:.1f}k".replace(".0k", "k")
    return f"{value / 1_000_000:.1f}M".replace(".0M", "M")
