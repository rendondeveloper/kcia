"""Task scope tests."""

from __future__ import annotations

import subprocess
from pathlib import Path

from kcia.waves.definitions import get_wave
from kcia.waves.prompts import build_prompt_with_stats
from kcia.waves.session import Session

ROOT = Path(__file__).resolve().parents[1]
KCIA = ROOT / ".venv" / "bin" / "kcia"
MELOS_REPO = ROOT / "tests" / "fixtures" / "repos" / "melos_mono"


def _init_scoped_session(tmp_path: Path, scope: list[str] | None = None) -> Session:
    repo = tmp_path / "melos"
    subprocess.run(["cp", "-R", str(MELOS_REPO), str(repo)], check=True)
    subprocess.run(
        [str(KCIA), "start", "--yes", "--path", str(repo)],
        check=True,
        capture_output=True,
    )
    return Session.create(
        repo,
        text="change api",
        mode="prompt",
        scope=scope or [],
    )


def test_scope_limits_profiles_in_prompt(tmp_path: Path) -> None:
    session = _init_scoped_session(tmp_path, scope=["packages/api"])
    prompt, _ = build_prompt_with_stats(get_wave("implementation"), session)
    assert "## Profile bundle: backend-dart" in prompt
    assert "## Profile bundle: mobile-flutter" not in prompt
    assert "## Profile bundle: web-flutter" not in prompt


def test_without_scope_keeps_cwd_resolved_profiles(tmp_path: Path) -> None:
    session = _init_scoped_session(tmp_path)
    prompt, _ = build_prompt_with_stats(get_wave("understanding"), session)
    assert "## Profile bundle: backend-dart" in prompt


def test_scope_reduces_tokens_vs_all_active_profiles(tmp_path: Path) -> None:
    repo = tmp_path / "melos"
    subprocess.run(["cp", "-R", str(MELOS_REPO), str(repo)], check=True)
    subprocess.run(
        [str(KCIA), "start", "--yes", "--path", str(repo)],
        check=True,
        capture_output=True,
    )
    scoped = Session.create(repo, text="api only", mode="prompt", scope=["packages/api"])
    all_profiles = Session.create(
        repo,
        text="all",
        mode="prompt",
        active_profiles=["backend-dart", "mobile-flutter", "web-flutter"],
    )
    scoped_total = sum(
        build_prompt_with_stats(wave, scoped)[1].total_tokens for wave in [get_wave("implementation")]
    )
    all_total = sum(
        build_prompt_with_stats(wave, all_profiles)[1].total_tokens
        for wave in [get_wave("implementation")]
    )
    assert scoped_total < all_total
    reduction = (all_total - scoped_total) / all_total
    assert reduction >= 0.25


def test_scope_missing_path_exits_before_session(tmp_path: Path) -> None:
    repo = tmp_path / "melos"
    subprocess.run(["cp", "-R", str(MELOS_REPO), str(repo)], check=True)
    (repo / ".git").mkdir()
    result = subprocess.run(
        [str(KCIA), "work", "demo", "--scope", "packages/missing"],
        capture_output=True,
        text=True,
        cwd=repo,
    )
    assert result.returncode == 1
    assert "Scope path does not exist" in result.stdout + result.stderr
    assert not (repo / ".ai" / "local" / "session.json").is_file()
