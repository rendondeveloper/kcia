import json
import shutil
from pathlib import Path

import yaml
from typer.testing import CliRunner

from kcia.commands.init import GITIGNORE_ENTRIES
from kcia.main import app

runner = CliRunner()
FIXTURES = Path(__file__).parent / "fixtures" / "repos"


def _repo(tmp_path: Path, name: str) -> Path:
    target = tmp_path / name
    shutil.copytree(FIXTURES / name, target)
    return target


def test_init_writes_manifest_bundles_and_adapters(tmp_path: Path) -> None:
    repo = _repo(tmp_path, "melos_mono")
    result = runner.invoke(app, ["init", "--yes", "--path", str(repo)])
    assert result.exit_code == 0, result.output
    assert "Detecting profiles…" in result.output

    manifest = yaml.safe_load((repo / ".ai" / "manifest.yaml").read_text())
    assert manifest["schema_version"] == 2
    assert manifest["project"]["layout"] == "monorepo"

    ids = {entry["id"] for entry in manifest["profiles"]}
    assert ids == {"backend-dart", "mobile-flutter", "web-flutter"}

    backend = next(e for e in manifest["profiles"] if e["id"] == "backend-dart")
    assert "packages/api/**" in backend["roots"]

    assert (repo / ".ai" / "generated" / "profiles" / "backend-dart" / "references.md").is_file()
    assert (repo / "CLAUDE.md").is_file()
    assert (repo / "AGENTS.md").is_file()
    assert (repo / ".cursor" / "rules" / "00-core.mdc").is_file()
    assert list((repo / ".cursor" / "rules").glob("*-backend-dart.mdc"))

    opencode = json.loads((repo / "opencode.json").read_text(encoding="utf-8"))
    assert opencode["$schema"] == "https://opencode.ai/config.json"
    assert ".ai/generated/profiles/backend-dart/**" in opencode["instructions"]
    assert "mcp" in opencode


def test_init_gitignores_everything_it_generates(tmp_path: Path) -> None:
    repo = _repo(tmp_path, "melos_mono")
    (repo / ".gitignore").write_text("build/\n", encoding="utf-8")

    runner.invoke(app, ["init", "--yes", "--path", str(repo)])

    lines = {line.strip() for line in (repo / ".gitignore").read_text().splitlines()}
    assert set(GITIGNORE_ENTRIES) <= lines
    assert ".ai/manifest.yaml" in lines
    assert "build/" in lines  # pre-existing entries are preserved


def test_init_creates_gitignore_when_absent(tmp_path: Path) -> None:
    repo = _repo(tmp_path, "dart_server")
    assert not (repo / ".gitignore").exists()

    runner.invoke(app, ["init", "--yes", "--path", str(repo)])

    lines = {line.strip() for line in (repo / ".gitignore").read_text().splitlines()}
    assert ".ai/local/" in lines


def test_init_is_idempotent(tmp_path: Path) -> None:
    repo = _repo(tmp_path, "melos_mono")
    runner.invoke(app, ["init", "--yes", "--path", str(repo)])
    snapshot = {
        path: path.read_bytes()
        for path in sorted(repo.rglob("*"))
        if path.is_file() and path.name != "manifest.yaml"
    }

    second = runner.invoke(app, ["init", "--yes", "--path", str(repo)])
    assert second.exit_code == 0
    assert "Already up to date" in second.output

    after = {
        path: path.read_bytes()
        for path in sorted(repo.rglob("*"))
        if path.is_file() and path.name != "manifest.yaml"
    }
    assert after == snapshot


def test_init_accepts_all_high_hits_on_ambiguity(tmp_path: Path) -> None:
    repo = _repo(tmp_path, "flutter_universal")
    result = runner.invoke(app, ["init", "--yes", "--path", str(repo)])
    assert result.exit_code == 0

    manifest = yaml.safe_load((repo / ".ai" / "manifest.yaml").read_text())
    ids = {entry["id"] for entry in manifest["profiles"]}
    assert {"mobile-flutter", "web-flutter"} <= ids


def test_init_reports_when_nothing_detected(tmp_path: Path) -> None:
    repo = tmp_path / "empty"
    repo.mkdir()
    result = runner.invoke(app, ["init", "--yes", "--path", str(repo)])
    assert result.exit_code == 1
    assert "Detecting profiles…" in result.output
    assert "No profiles detected" in result.output


def test_cursor_globs_are_scoped_to_roots(tmp_path: Path) -> None:
    repo = _repo(tmp_path, "melos_mono")
    runner.invoke(app, ["init", "--yes", "--path", str(repo)])

    rule = next((repo / ".cursor" / "rules").glob("*-backend-dart.mdc")).read_text()
    assert "packages/api/**/*.dart" in rule
