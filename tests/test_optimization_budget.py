"""End-to-end optimization budget verification."""

from __future__ import annotations

from kcia.waves.definitions import load_waves
from kcia.waves.prompts import build_prompt_with_stats

PHASE0_TASK_TOKENS = 14834


def test_total_task_input_is_below_target(melos_session) -> None:
    """Suma de los 5 prompts de una tarea. Línea base commit 110af3b: ~14818 tokens."""
    total = sum(
        build_prompt_with_stats(wave, melos_session)[1].total_tokens
        for wave in load_waves()
    )
    # 12700 -> 12710: project.md pasó de `TODO` a hechos derivados del repositorio.
    # 12710 -> 12935: protocolo `BLOCKED:` en las 5 waves. Es contexto que compra
    # correctitud —parar en vez de razonar sobre un hueco— no ruido de guía.
    assert total <= 12935
    reduction = (PHASE0_TASK_TOKENS - total) / PHASE0_TASK_TOKENS
    assert reduction >= 0.12

