"""Wave definition loading."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from kcia.paths import control_plane_root

WAVES_FILE = "waves.yaml"
PROMPTS_DIR = "prompts"


@dataclass(frozen=True)
class BudgetConfig:
    max_prompt_tokens: int
    drop_order: tuple[str, ...]


@dataclass(frozen=True)
class WaveDefinition:
    id: str
    order: int
    agent: str
    allow_edits: bool
    writes: tuple[str, ...]
    requires: tuple[str, ...]
    description: str
    prompt_template: str
    edit_scope: tuple[str, ...] = ()
    validation: str | None = None
    can_ask_questions: bool = False
    reference_tags: tuple[str, ...] | None = None
    requires_approval: bool = False
    approval_shows: str | None = None


def waves_root() -> Path:
    return control_plane_root() / "waves"


def prompts_dir() -> Path:
    return waves_root() / PROMPTS_DIR


def load_budget_config() -> BudgetConfig:
    from kcia.waves.budget import DEFAULT_DROP_ORDER, DEFAULT_MAX_PROMPT_TOKENS

    path = waves_root() / WAVES_FILE
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw = data.get("budget") or {}
    return BudgetConfig(
        max_prompt_tokens=int(raw.get("max_prompt_tokens", DEFAULT_MAX_PROMPT_TOKENS)),
        drop_order=tuple(raw.get("drop_order", DEFAULT_DROP_ORDER)),
    )


def load_waves() -> list[WaveDefinition]:
    path = waves_root() / WAVES_FILE
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    waves: list[WaveDefinition] = []
    for raw in data.get("waves", []):
        raw_tags = raw.get("reference_tags")
        reference_tags = None if "reference_tags" not in raw else tuple(raw_tags or [])
        waves.append(
            WaveDefinition(
                id=raw["id"],
                order=int(raw["order"]),
                agent=raw["agent"],
                allow_edits=bool(raw.get("allow_edits", False)),
                writes=tuple(raw.get("writes", [])),
                requires=tuple(raw.get("requires", [])),
                description=raw.get("description", ""),
                prompt_template=raw["prompt_template"],
                edit_scope=tuple(raw.get("edit_scope", [])),
                validation=raw.get("validation"),
                can_ask_questions=bool(raw.get("can_ask_questions", False)),
                reference_tags=reference_tags,
                requires_approval=bool(raw.get("requires_approval", False)),
                approval_shows=raw.get("approval_shows"),
            )
        )
    return sorted(waves, key=lambda wave: wave.order)


def get_wave(wave_id: str) -> WaveDefinition:
    for wave in load_waves():
        if wave.id == wave_id:
            return wave
    available = ", ".join(wave.id for wave in load_waves())
    raise KeyError(f"unknown wave '{wave_id}'; available: {available}")
