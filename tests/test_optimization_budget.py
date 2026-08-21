"""End-to-end optimization budget verification."""

from __future__ import annotations

from kcia.waves.definitions import load_waves
from kcia.waves.prompts import build_prompt_with_stats

PHASE0_TASK_TOKENS = 14834


def test_total_task_input_is_below_target(melos_session) -> None:
    """Sum of the 5 prompts for one task. Baseline commit 110af3b: ~14818 tokens."""
    total = sum(
        build_prompt_with_stats(wave, melos_session)[1].total_tokens
        for wave in load_waves()
    )
    # 12700 -> 12710: project.md moved from `TODO` to facts derived from the repository.
    # 12710 -> 12935: `BLOCKED:` protocol on all 5 waves. That context buys
    # correctness —stop instead of reasoning over a gap— not guidance noise.
    # 12935 -> 13120: analysis wave documents `depends_on` and integration checklist.
    assert total <= 13120
    reduction = (PHASE0_TASK_TOKENS - total) / PHASE0_TASK_TOKENS
    # Slightly below 12% after analysis prompt grew for multi-profile ordering.
    assert reduction >= 0.115

