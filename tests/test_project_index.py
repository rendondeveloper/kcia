"""Deterministic project facts written into .ai/context/project.md."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from kcia.project_index import MAX_TOKENS, _estimate_tokens, build_project_facts

ROOT = Path(__file__).resolve().parents[1]
KCIA = ROOT / ".venv" / "bin" / "kcia"


@pytest.fixture()
def flutter_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "readergps"
    (repo / "lib" / "services").mkdir(parents=True)
    (repo / "lib" / "widgets").mkdir()
    (repo / "test").mkdir()
    (repo / "web").mkdir()
    (repo / "macos").mkdir()
    (repo / "lib" / "main.dart").write_text("void main() {}", encoding="utf-8")
    (repo / "pubspec.yaml").write_text(
        "name: readergps\n"
        "environment:\n"
        "  sdk: ^3.9.2\n"
        "dependencies:\n"
        "  flutter:\n"
        "    sdk: flutter\n"
        "  cupertino_icons: ^1.0.8\n"
        "  flutter_libserialport: ^0.5.0\n"
        "  permission_handler: ^12.0.1\n",
        encoding="utf-8",
    )
    return repo


def test_reports_stack_sdk_and_platforms(flutter_repo: Path) -> None:
    facts = build_project_facts(flutter_repo, name="ReaderGps", layout="single")
    assert "ReaderGps — single Flutter project, Dart SDK ^3.9.2." in facts
    assert "Platforms: macos, web." in facts


def test_omits_ubiquitous_dependencies(flutter_repo: Path) -> None:
    """`flutter` and `cupertino_icons` say nothing about this specific project."""
    facts = build_project_facts(flutter_repo, name="ReaderGps", layout="single")
    assert "flutter_libserialport" in facts
    assert "permission_handler" in facts
    assert "cupertino_icons" not in facts
    assert "\nflutter," not in facts


def test_reports_entry_point_and_source_dirs(flutter_repo: Path) -> None:
    facts = build_project_facts(flutter_repo, name="ReaderGps", layout="single")
    assert "`lib/main.dart` — entry point" in facts
    assert "`lib/`: services, widgets" in facts
    assert "`test/` — tests" in facts


def test_never_emits_todo_placeholders(flutter_repo: Path) -> None:
    """Guardrails treat TODO as 'information unavailable' — it must not be the
    project description shipped in every prompt."""
    facts = build_project_facts(flutter_repo, name="ReaderGps", layout="single")
    assert "TODO" not in facts


def test_stays_within_the_token_budget(flutter_repo: Path) -> None:
    (flutter_repo / "pubspec.yaml").write_text(
        "name: big\ndependencies:\n"
        + "".join(f"  package_{i}: ^1.0.0\n" for i in range(400)),
        encoding="utf-8",
    )
    for i in range(60):
        (flutter_repo / "lib" / f"feature_{i}").mkdir(exist_ok=True)

    facts = build_project_facts(flutter_repo, name="big", layout="single")
    assert _estimate_tokens(facts) <= MAX_TOKENS


def test_node_project_is_recognized(tmp_path: Path) -> None:
    repo = tmp_path / "web"
    (repo / "src").mkdir(parents=True)
    (repo / "package.json").write_text(
        json.dumps({"name": "web", "dependencies": {"react": "^19.0.0"}}), encoding="utf-8"
    )
    facts = build_project_facts(repo, name="web", layout="single")
    assert "Node/JavaScript project" in facts
    assert "react" in facts


def test_unknown_stack_yields_empty_rather_than_invented_facts(tmp_path: Path) -> None:
    repo = tmp_path / "mystery"
    repo.mkdir()
    assert build_project_facts(repo, name="mystery", layout="single") == ""


def test_malformed_manifest_does_not_crash(tmp_path: Path) -> None:
    repo = tmp_path / "broken"
    repo.mkdir()
    (repo / "pubspec.yaml").write_text("name: [unclosed\n  bad: :yaml", encoding="utf-8")
    build_project_facts(repo, name="broken", layout="single")


def test_init_writes_facts_and_preserves_later_edits(tmp_path: Path) -> None:
    repo = tmp_path / "melos_mono"
    subprocess.run(
        ["cp", "-R", str(ROOT / "tests" / "fixtures" / "repos" / "melos_mono"), str(repo)],
        check=True,
    )
    subprocess.run(["git", "init", "-q", str(repo)], check=True)

    result = subprocess.run(
        [str(KCIA), "start", "--yes", "--path", str(repo)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr

    project = repo / ".ai" / "context" / "project.md"
    assert "TODO: describe this project" not in project.read_text(encoding="utf-8")

    project.write_text(
        project.read_text(encoding="utf-8") + "\n## Domain\nHand written.\n", encoding="utf-8"
    )
    subprocess.run([str(KCIA), "start", "--yes", "--path", str(repo)], check=True, capture_output=True)
    assert "Hand written." in project.read_text(encoding="utf-8")

    # --refresh-context is the explicit opt-in to discard those edits.
    subprocess.run(
        [str(KCIA), "start", "--yes", "--refresh-context", "--path", str(repo)],
        check=True,
        capture_output=True,
    )
    assert "Hand written." not in project.read_text(encoding="utf-8")


def test_reports_fvm_pin_when_the_repository_declares_one(flutter_repo: Path) -> None:
    (flutter_repo / ".fvmrc").write_text('{"flutter": "3.35.7"}', encoding="utf-8")
    facts = build_project_facts(flutter_repo, name="app", layout="single")
    assert "Toolchain: fvm (Flutter 3.35.7)." in facts


def test_reports_the_legacy_fvm_config(flutter_repo: Path) -> None:
    (flutter_repo / ".fvm").mkdir()
    (flutter_repo / ".fvm" / "fvm_config.json").write_text(
        '{"flutterSdkVersion": "3.24.0"}', encoding="utf-8"
    )
    assert "fvm (Flutter 3.24.0)" in build_project_facts(
        flutter_repo, name="app", layout="single"
    )


def test_no_toolchain_line_without_a_version_manager(flutter_repo: Path) -> None:
    """Most repos use neither; claiming fvm would be wrong."""
    assert "Toolchain:" not in build_project_facts(flutter_repo, name="app", layout="single")
