"""Prompt composition regression and baseline tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from kcia.waves.definitions import get_wave, load_waves
from kcia.waves.prompts import build_prompt, build_prompt_with_stats

ROOT = Path(__file__).resolve().parents[1]
BASELINE_FIXTURE = ROOT / "tests" / "fixtures" / "prompts" / "understanding-baseline.md"
PHASE0_UNDERSTANDING_TOKENS = 2955
# 2482 -> 2491: sección `task-statement`, el enunciado nunca llegaba al prompt.
# 2491 -> 2493: project.md pasó de `TODO` a hechos derivados del repositorio.
# 2493 -> 2538: protocolo `BLOCKED:`, 45 tokens por wave (225 por tarea).
# Ninguna es una regresión de contexto. El protocolo se paga solo: evita corridas
# completas —12.7k tokens medidos— sobre una wave que ya declaró no poder seguir.
PHASE1_UNDERSTANDING_TOKENS = 2538


def test_baseline_prompt_size(melos_session) -> None:
    """Ancla la línea base. Este test DEBE actualizarse conscientemente en cada fase
    que reduzca tokens, y su valor solo puede BAJAR."""
    _, stats = build_prompt_with_stats(get_wave("understanding"), melos_session)
    assert 2400 <= stats.total_tokens <= 2600
    assert stats.total_tokens <= PHASE1_UNDERSTANDING_TOKENS


def test_understanding_tokens_reduced_from_phase0(melos_session) -> None:
    _, stats = build_prompt_with_stats(get_wave("understanding"), melos_session)
    reduction = (PHASE0_UNDERSTANDING_TOKENS - stats.total_tokens) / PHASE0_UNDERSTANDING_TOKENS
    assert reduction >= 0.14


def test_build_prompt_matches_frozen_baseline(melos_session) -> None:
    wave = get_wave("understanding")
    prompt = build_prompt(wave, melos_session)
    expected = BASELINE_FIXTURE.read_text(encoding="utf-8")
    assert prompt == expected


def test_build_prompt_with_stats_section_names(melos_session) -> None:
    _, stats = build_prompt_with_stats(get_wave("understanding"), melos_session)
    names = [section.name for section in stats.sections]
    assert names[:4] == ["role", "guardrails", "task-statement", "project-context"]
    assert names[4] == "repo-map"
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



def test_prompt_mode_statement_reaches_every_wave(melos_session) -> None:
    """El enunciado vive en session.json; en modo prompt nadie escribe
    .ai/context/task.md, así que cada wave corría sin problema que resolver."""
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
