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
    # 13120 -> 13420: analysis wave gained the plan-metadata YAML block (title/type/
    # ticket/summary/affected_files/acceptance_criteria) that branch/commit/session
    # metadata now derives from instead of the raw prompt; documentation-final gained
    # the matching deviation-check instruction.
    # 13420 -> 15035: _dart-core guardrails (layer responsibilities, state-manager
    # consumption, boundary-value testing) surfaced in profile references.
    # 15035 -> 16719: canonical feature layout, barrel topology, and mapper
    # snippets added to architecture.md (analysis wave) plus a short coding
    # cross-ref.
    assert total <= 16719
    # Layout content exceeds phase-0; absolute cap until a trim pass elsewhere.
    assert total <= PHASE0_TASK_TOKENS * 1.13

