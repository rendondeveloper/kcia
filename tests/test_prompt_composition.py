"""Prompt composition regression and baseline tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from kcia.waves.definitions import get_wave, load_waves
from kcia.waves.prompts import build_prompt, build_prompt_with_stats

ROOT = Path(__file__).resolve().parents[1]
BASELINE_FIXTURE = ROOT / "tests" / "fixtures" / "prompts" / "understanding-baseline.md"
PHASE0_UNDERSTANDING_TOKENS = 2955


def test_baseline_prompt_size(melos_session) -> None:
    """Ancla la línea base. Este test DEBE actualizarse conscientemente en cada fase
    que reduzca tokens, y su valor solo puede BAJAR."""
    _, stats = build_prompt_with_stats(get_wave("understanding"), melos_session)
    assert 2300 <= stats.total_tokens <= 2400
    assert stats.total_tokens <= 2380


def test_understanding_tokens_reduced_from_phase0(melos_session) -> None:
    _, stats = build_prompt_with_stats(get_wave("understanding"), melos_session)
    reduction = (PHASE0_UNDERSTANDING_TOKENS - stats.total_tokens) / PHASE0_UNDERSTANDING_TOKENS
    assert reduction >= 0.19


def test_build_prompt_matches_frozen_baseline(melos_session) -> None:
    wave = get_wave("understanding")
    prompt = build_prompt(wave, melos_session)
    expected = BASELINE_FIXTURE.read_text(encoding="utf-8")
    assert prompt == expected


def test_build_prompt_with_stats_section_names(melos_session) -> None:
    _, stats = build_prompt_with_stats(get_wave("understanding"), melos_session)
    names = [section.name for section in stats.sections]
    assert names[:3] == ["role", "guardrails", "project-context"]
    assert any(name.startswith("profile:") for name in names)
    assert names[-2:] == ["wave-instruction", "output-format"]


def test_understanding_excludes_architecture_reference(melos_session) -> None:
    prompt, _ = build_prompt_with_stats(get_wave("understanding"), melos_session)
    assert "Respeta clean architecture" not in prompt


def test_analysis_includes_architecture_reference(melos_session) -> None:
    prompt, _ = build_prompt_with_stats(get_wave("analysis"), melos_session)
    assert "Respeta clean architecture" in prompt


def test_documentation_init_has_rules_but_no_profile_references(melos_session) -> None:
    prompt, _ = build_prompt_with_stats(get_wave("documentation-init"), melos_session)
    assert "### Rules" in prompt
    assert "Respeta clean architecture" not in prompt
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
    assert "Respeta clean architecture" in prompt


def test_wave_with_empty_reference_tags_injects_none(melos_session) -> None:
    wave = get_wave("documentation-init")
    assert wave.reference_tags == ()
    prompt, _ = build_prompt_with_stats(wave, melos_session)
    assert "Respeta clean architecture" not in prompt

