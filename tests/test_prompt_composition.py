"""Prompt composition regression and baseline tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from kcia.waves.definitions import get_wave, load_waves
from kcia.waves.prompts import build_prompt, build_prompt_with_stats

ROOT = Path(__file__).resolve().parents[1]
BASELINE_FIXTURE = ROOT / "tests" / "fixtures" / "prompts" / "understanding-baseline.md"
PHASE0_UNDERSTANDING_TOKENS = 2955
# 2482 -> 2491: `task-statement` section; the problem statement never reached the prompt.
# 2491 -> 2493: project.md moved from `TODO` to facts derived from the repository.
# 2493 -> 2538: `BLOCKED:` protocol, 45 tokens per wave (225 per task).
# 2538 -> 2863: _dart-core guardrails (state-manager consumption, boundary-value
# correctness) added to coding reference injected into understanding wave.
# 2863 -> 2909: coding.md cross-ref to architecture feature layout / mapper.
PHASE1_UNDERSTANDING_TOKENS = 2909


def test_baseline_prompt_size(melos_session) -> None:
    """Anchor the baseline. This test MUST be updated deliberately on each phase
    that reduces tokens, and its value may only go DOWN."""
    _, stats = build_prompt_with_stats(get_wave("understanding"), melos_session)
    assert 2850 <= stats.total_tokens <= 2950
    assert stats.total_tokens <= PHASE1_UNDERSTANDING_TOKENS


def test_understanding_tokens_reduced_from_phase0(melos_session) -> None:
    _, stats = build_prompt_with_stats(get_wave("understanding"), melos_session)
    reduction = (PHASE0_UNDERSTANDING_TOKENS - stats.total_tokens) / PHASE0_UNDERSTANDING_TOKENS
    # Guardrail/layout additions in _dart-core largely offset the phase-1 gain;
    # still keep a small reduction vs phase-0.
    assert reduction >= 0.01


def test_build_prompt_matches_frozen_baseline(melos_session) -> None:
    wave = get_wave("understanding")
    prompt = build_prompt(wave, melos_session)
    expected = BASELINE_FIXTURE.read_text(encoding="utf-8")
    assert prompt == expected


def test_build_prompt_with_stats_section_names(melos_session) -> None:
    _, stats = build_prompt_with_stats(get_wave("understanding"), melos_session)
    names = [section.name for section in stats.sections]
    assert names[:4] == ["role", "guardrails", "task-statement", "related-history"]
    assert names[4] == "project-context"
    assert names[5] == "repo-map"
    assert any(name.startswith("profile:") for name in names)
    assert names[-2:] == ["wave-instruction", "output-format"]


def test_build_prompt_static_prefix_before_dynamic_sections(melos_session) -> None:
    prompt, _ = build_prompt_with_stats(get_wave("understanding"), melos_session)
    profile_pos = prompt.index("## Profile bundle:")
    task_pos = prompt.index("## Task statement")
    repo_map_pos = prompt.index("## Repository map")
    assert profile_pos < task_pos < repo_map_pos


def test_understanding_excludes_architecture_reference(melos_session) -> None:
    prompt, _ = build_prompt_with_stats(get_wave("understanding"), melos_session)
    assert "Follow clean architecture" not in prompt


def test_analysis_includes_architecture_reference(melos_session) -> None:
    prompt, _ = build_prompt_with_stats(get_wave("analysis"), melos_session)
    assert "Follow clean architecture" in prompt


def test_documentation_init_has_rules_but_no_profile_references(melos_session) -> None:
    prompt, _ = build_prompt_with_stats(get_wave("documentation-init"), melos_session)
    assert "### Rules" in prompt
    assert "Follow clean architecture" not in prompt
    assert "Estándares de código Dart" not in prompt


def test_wave_without_reference_tags_injects_all(tmp_path, melos_session) -> None:
    from kcia.waves.definitions import WaveDefinition

    wave = WaveDefinition(
        id="custom",
        order=99,
        agent="planner",
        allow_edits=False,
        writes=(),
        requires=(),
        description="",
        prompt_template="understanding.md.j2",
        reference_tags=None,
    )
    prompt, _ = build_prompt_with_stats(wave, melos_session)
    assert "Follow clean architecture" in prompt


def test_wave_with_empty_reference_tags_injects_none(melos_session) -> None:
    wave = get_wave("documentation-init")
    assert wave.reference_tags == ()
    prompt, _ = build_prompt_with_stats(wave, melos_session)
    assert "Follow clean architecture" not in prompt



def test_prompt_mode_statement_reaches_every_wave(melos_session) -> None:
    """The statement lives in session.json; in prompt mode nobody writes
    .ai/context/task.md, so every wave used to run without a problem to solve."""
    for wave in load_waves():
        prompt = build_prompt(wave, melos_session)
        assert "arregla el overflow" in prompt, f"missing statement in wave {wave.id}"


def test_ticket_mode_statement_does_not_repeat_the_key(melos_session) -> None:
    from kcia.waves.session import Session

    session = Session.create(
        melos_session.repo_root,
        text="PROJ-42",
        mode="ticket",
        ticket_key="PROJ-42",
    )
    prompt = build_prompt(get_wave("understanding"), session)
    assert "Ticket: `PROJ-42`" in prompt
    assert prompt.count("PROJ-42") == 1


def test_scope_is_stated_in_the_prompt(melos_session) -> None:
    from kcia.waves.session import Session

    session = Session.create(
        melos_session.repo_root,
        text="arregla el overflow",
        mode="prompt",
        scope=["packages/app"],
    )
    prompt = build_prompt(get_wave("understanding"), session)
    assert "Scope is limited to: packages/app" in prompt
