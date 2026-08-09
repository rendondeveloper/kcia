"""Context budget dropping tests."""

from __future__ import annotations

from unittest.mock import patch

from kcia.profiles.inheritance import ReferenceEntry
from kcia.waves.budget import apply_budget
from kcia.waves.definitions import get_wave
from kcia.waves.prompts import build_prompt_with_stats
from kcia.waves.session import Session


def _entry(tmp_path: Path, name: str, text: str, tags: tuple[str, ...]) -> ReferenceEntry:
    path = tmp_path / f"{name}.md"
    path.write_text(text, encoding="utf-8")
    return ReferenceEntry(profile_id="demo", path=path, tags=tags)


def test_apply_budget_generous_keeps_everything(tmp_path: Path) -> None:
    entries = [_entry(tmp_path, "a", "a" * 40, ("architecture",))]
    kept, dropped = apply_budget(entries, fixed_tokens=10, max_tokens=10_000, drop_order=["architecture"])
    assert kept == entries
    assert dropped == []


def test_apply_budget_tiny_drops_in_drop_order(tmp_path: Path) -> None:
    entries = [
        _entry(tmp_path, "arch", "a" * 400, ("architecture",)),
        _entry(tmp_path, "mono", "b" * 400, ("monorepo",)),
        _entry(tmp_path, "code", "c" * 400, ("coding",)),
    ]
    kept, dropped = apply_budget(
        entries,
        fixed_tokens=0,
        max_tokens=150,
        drop_order=["architecture", "monorepo", "data", "api", "web", "accessibility", "testing", "validation", "coding"],
    )
    dropped_names = [entry.path.stem for entry in dropped]
    assert dropped_names == ["arch", "mono"]
    assert [entry.path.stem for entry in kept] == ["code"]


def test_melos_default_budget_drops_nothing(melos_session) -> None:
    _, stats = build_prompt_with_stats(get_wave("understanding"), melos_session)
    assert stats.dropped_tokens == 0


def test_tiny_budget_adds_context_block_and_persists_session(melos_session) -> None:
    with patch("kcia.waves.prompts.resolve_max_prompt_tokens", return_value=500):
        prompt, stats = build_prompt_with_stats(get_wave("analysis"), melos_session)
    assert stats.dropped_tokens > 0
    assert "## Context budget" in prompt
    assert melos_session.waves["analysis"].get("dropped_references")


def test_guardrails_remain_with_absurd_budget(melos_session) -> None:
    with patch("kcia.waves.prompts.resolve_max_prompt_tokens", return_value=100):
        prompt, _ = build_prompt_with_stats(get_wave("understanding"), melos_session)
    assert "## Guardrails" in prompt
