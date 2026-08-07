"""Token usage aggregation and formatting."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class UsageTotals:
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    tool_calls: int = 0
    provider_calls: int = 0
    per_wave: dict[str, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens


def collect_usage(waves: dict[str, dict[str, Any]]) -> UsageTotals:
    """Sum the token counters persisted on each wave of a session."""
    totals = UsageTotals()
    for wave_id, state in waves.items():
        tokens = int(state.get("tokens") or 0)
        if tokens:
            totals.per_wave[wave_id] = tokens
        totals.input_tokens += int(state.get("input_tokens") or 0)
        totals.output_tokens += int(state.get("output_tokens") or 0)
        totals.cached_tokens += int(state.get("cached_tokens") or 0)
        totals.tool_calls += int(state.get("tool_calls") or 0)
        totals.provider_calls += int(state.get("provider_calls") or 0)
    return totals


def format_tokens(value: int) -> str:
    """Compact, readable token count: 1234 -> 1.2k, 1200000 -> 1.2M."""
    if value < 1_000:
        return str(value)
    if value < 1_000_000:
        return f"{value / 1_000:.1f}k".replace(".0k", "k")
    return f"{value / 1_000_000:.1f}M".replace(".0M", "M")
