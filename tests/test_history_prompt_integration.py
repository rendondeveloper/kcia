"""Integration tests for related session history in wave prompts."""

from __future__ import annotations

from kcia.history import index, log
from kcia.waves.definitions import get_wave
from kcia.waves.prompts import build_prompt
from kcia.waves.session import Session


def _log_overflow_session(repo_root, *, title: str = "Fix layout overflow") -> log.SessionEntry:
    entry = log.SessionEntry(
        id=log.new_id(),
        timestamp="2026-08-14T10:00:00Z",
        title=title,
        summary="Reduced header padding to stop the profile screen from overflowing.",
        decisions=["Prefer flex shrink over fixed heights"],
        files=[{"path": "packages/app/lib/profile.dart", "change": "modified"}],
        commit_sha=None,
        branch="main",
        task_id=None,
    )
    log.append_entry(repo_root, entry)
    index.sync(repo_root, entry)
    return entry


def test_understanding_includes_related_history(melos_session) -> None:
    entry = _log_overflow_session(melos_session.repo_root)
    session = Session.create(
        melos_session.repo_root,
        text="layout overflow on profile screen",
        mode="prompt",
    )

    prompt = build_prompt(get_wave("understanding"), session)

    assert "## Related history" in prompt
    assert entry.title in prompt


def test_analysis_excludes_related_history(melos_session) -> None:
    _log_overflow_session(melos_session.repo_root)

    prompt = build_prompt(get_wave("analysis"), melos_session)

    assert "## Related history" not in prompt


def test_fresh_repo_has_no_related_history_section(melos_session) -> None:
    prompt = build_prompt(get_wave("understanding"), melos_session)

    assert "## Related history" not in prompt


def test_punctuation_in_task_title_does_not_break_prompt(melos_session) -> None:
    _log_overflow_session(melos_session.repo_root, title="fix: header (overflow)")

    session = Session.create(
        melos_session.repo_root,
        text="fix: header (overflow)",
        mode="prompt",
    )
    prompt = build_prompt(get_wave("understanding"), session)

    assert "arregla el overflow" not in prompt
    assert "fix: header (overflow)" in prompt
