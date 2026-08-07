"""Prompt composition regression and baseline tests."""

from __future__ import annotations

from pathlib import Path

from kcia.waves.definitions import get_wave
from kcia.waves.prompts import build_prompt, build_prompt_with_stats

ROOT = Path(__file__).resolve().parents[1]
BASELINE_FIXTURE = ROOT / "tests" / "fixtures" / "prompts" / "understanding-baseline.md"


def test_baseline_prompt_size(melos_session) -> None:
    """Ancla la línea base. Este test DEBE actualizarse conscientemente en cada fase
    que reduzca tokens, y su valor solo puede BAJAR."""
    _, stats = build_prompt_with_stats(get_wave("understanding"), melos_session)
    assert 2900 <= stats.total_tokens <= 3000
    assert stats.total_tokens <= 3100


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
