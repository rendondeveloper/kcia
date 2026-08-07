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
    # 12700 -> 12710 al sustituir los `TODO` de project.md por hechos derivados
    # del repositorio: mismo bloque, ahora con información real en vez de un
    # marcador que los guardrails definen como "dato no disponible".
    assert total <= 12710
    reduction = (PHASE0_TASK_TOKENS - total) / PHASE0_TASK_TOKENS
    assert reduction >= 0.14

