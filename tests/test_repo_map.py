"""Repository map prompt section tests."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from kcia.profiles.loader import load_registry
from kcia.profiles.schema import Manifest
from kcia.waves.definitions import get_wave
from kcia.waves.prompts import build_prompt_with_stats
from kcia.waves.repomap import build_repo_map
from kcia.waves.session import Session

ROOT = Path(__file__).resolve().parents[1]
KCIA = ROOT / ".venv" / "bin" / "kcia"
MELOS = ROOT / "tests" / "fixtures" / "repos" / "melos_mono"
DART_SERVER = ROOT / "tests" / "fixtures" / "repos" / "dart_server"


def test_melos_repo_map_lists_packages(melos_session) -> None:
    prompt, stats = build_prompt_with_stats(get_wave("understanding"), melos_session)
    assert "## Repository map" in prompt
    assert "| packages/api | backend-dart |" in prompt
    api_row = next(line for line in prompt.splitlines() if "packages/api" in line)
    assert "`fvm dart test`" in api_row
    assert "flutter test" not in api_row
    repo_map = next(section for section in stats.sections if section.name == "repo-map")
    assert repo_map.tokens < 400


def test_single_package_layout(tmp_path: Path) -> None:
    repo = tmp_path / "dart"
    shutil.copytree(DART_SERVER, repo)
    subprocess.run([str(KCIA), "init", "--yes", "--path", str(repo)], check=True, capture_output=True)
    manifest = Manifest.model_validate(
        __import__("yaml").safe_load((repo / ".ai" / "manifest.yaml").read_text())
    )
    repo_map = build_repo_map(manifest, load_registry(repo), repo)
    assert "Layout: single" in repo_map
    assert repo_map.count("|") >= 4


def test_empty_manifest_profiles_returns_empty_map() -> None:
    manifest = Manifest.model_validate({"schema_version": 2, "profiles": []})
    assert build_repo_map(manifest, load_registry(None), Path("/tmp")) == ""


def test_prompt_without_profiles_has_no_repo_map_header(tmp_path: Path) -> None:
    repo = tmp_path / "empty"
    repo.mkdir()
    (repo / ".git").mkdir()
    session = Session.create(repo, text="x", mode="prompt")
    prompt, _ = build_prompt_with_stats(get_wave("understanding"), session)
    assert "## Repository map" not in prompt
