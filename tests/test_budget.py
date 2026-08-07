"""Unit tests for token estimation and prompt stats."""

from __future__ import annotations

from kcia.waves.budget import (
    CHARS_PER_TOKEN,
    PromptStats,
    SectionStat,
    estimate_tokens,
)


def test_estimate_tokens_empty() -> None:
    assert estimate_tokens("") == 0


def test_estimate_tokens_uses_chars_per_token() -> None:
    text = "a" * CHARS_PER_TOKEN
    assert estimate_tokens(text) == 1
    assert estimate_tokens("a" * (CHARS_PER_TOKEN * 3 + 2)) == 3


def test_prompt_stats_total_and_dropped() -> None:
    stats = PromptStats(
        sections=[
            SectionStat(name="role", chars=100, tokens=25),
            SectionStat(name="guardrails", chars=200, tokens=50, dropped=True),
            SectionStat(name="task-context", chars=80, tokens=20),
        ]
    )
    assert stats.total_tokens == 95
    assert stats.dropped_tokens == 50


def test_prompt_stats_as_dict() -> None:
    stats = PromptStats(
        sections=[SectionStat(name="role", chars=40, tokens=10)]
    )
    data = stats.as_dict()
    assert data["total_tokens"] == 10
    assert data["dropped_tokens"] == 0
    assert data["sections"] == [
        {"name": "role", "chars": 40, "tokens": 10, "dropped": False}
    ]
