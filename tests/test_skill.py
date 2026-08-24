"""Tests for `kcia skill` and the local skills catalog."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from typer.testing import CliRunner

from kcia.main import app
from kcia.skills.catalog import load_catalog, skills_path
from kcia.waves.session import Session, session_path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "repos"
KCIA = ROOT / ".venv" / "bin" / "kcia"
runner = CliRunner()


def _repo(tmp_path: Path, name: str) -> Path:
    target = tmp_path / name
    shutil.copytree(FIXTURES / name, target)
    return target


def _init(repo: Path) -> None:
    result = runner.invoke(app, ["init", "--yes", "--path", str(repo)])
    assert result.exit_code == 0, result.output


def _write_skill(repo: Path, name: str) -> Path:
    skill_dir = repo / ".cursor" / "skills" / name
    skill_dir.mkdir(parents=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(f"# {name}\n", encoding="utf-8")
    return skill_file


@pytest.fixture
def melos_repo(tmp_path: Path, monkeypatch) -> Path:
    repo = _repo(tmp_path, "melos_mono")
    _init(repo)
    monkeypatch.chdir(repo)
    return repo


def test_register_by_path_found(melos_repo: Path) -> None:
    skill_file = _write_skill(melos_repo, "deploy_staging_services")
    result = runner.invoke(
        app,
        [
            "skill",
            "--backend",
            "--path",
            str(skill_file.parent),
            "--deploy",
        ],
    )
    assert result.exit_code == 0, result.output
    catalog = load_catalog(melos_repo)
    assert catalog.profiles["backend"]["deploy"].name == "deploy_staging_services"
    assert catalog.profiles["backend"]["deploy"].path.endswith(
        ".cursor/skills/deploy_staging_services/SKILL.md"
    )


def test_register_by_path_missing(melos_repo: Path) -> None:
    result = runner.invoke(
        app,
        ["skill", "--backend", "--path", ".cursor/skills/missing", "--deploy"],
    )
    assert result.exit_code == 1, result.output
    assert "not found" in result.output.lower()
    assert not skills_path(melos_repo).is_file()


def test_register_by_name_found(melos_repo: Path) -> None:
    _write_skill(melos_repo, "mote_kill")
    result = runner.invoke(app, ["skill", "--backend", "mote_kill"])
    assert result.exit_code == 0, result.output
    catalog = load_catalog(melos_repo)
    assert "mote_kill" in catalog.profiles["backend"]


def test_register_by_name_missing(melos_repo: Path) -> None:
    result = runner.invoke(app, ["skill", "--backend", "missing_skill"])
    assert result.exit_code == 1, result.output
    assert "not found" in result.output.lower()


def test_register_backend_on_mobile_only_repo(tmp_path: Path, monkeypatch) -> None:
    repo = _repo(tmp_path, "flutter_mobile")
    _init(repo)
    monkeypatch.chdir(repo)
    skill_file = _write_skill(repo, "deploy")
    result = runner.invoke(
        app,
        ["skill", "--backend", "--path", str(skill_file.parent), "--deploy"],
    )
    assert result.exit_code == 1, result.output
    assert "not active" in result.output.lower()


def test_reregister_same_shortcut_is_error(melos_repo: Path) -> None:
    skill_file = _write_skill(melos_repo, "deploy_staging_services")
    runner.invoke(
        app,
        ["skill", "--backend", "--path", str(skill_file.parent), "--deploy"],
    )
    result = runner.invoke(
        app,
        ["skill", "--backend", "--path", str(skill_file.parent), "--deploy"],
    )
    assert result.exit_code == 1, result.output
    assert "already registered" in result.output.lower()


def test_list_backend_and_all(melos_repo: Path) -> None:
    skill_file = _write_skill(melos_repo, "deploy_staging_services")
    runner.invoke(
        app,
        ["skill", "--backend", "--path", str(skill_file.parent), "--deploy"],
    )
    backend = runner.invoke(app, ["skill", "--backend"])
    assert backend.exit_code == 0, backend.output
    assert "deploy:" in backend.output

    all_ns = runner.invoke(app, ["skill"])
    assert all_ns.exit_code == 0, all_ns.output
    assert "[backend]" in all_ns.output


def test_remove_existing_and_missing(melos_repo: Path) -> None:
    skill_file = _write_skill(melos_repo, "deploy_staging_services")
    runner.invoke(
        app,
        ["skill", "--backend", "--path", str(skill_file.parent), "--deploy"],
    )
    removed = runner.invoke(app, ["skill", "--backend", "--deploy", "--remove"])
    assert removed.exit_code == 0, removed.output
    assert "Removed" in removed.output

    missing = runner.invoke(app, ["skill", "--backend", "--deploy", "--remove"])
    assert missing.exit_code == 1, missing.output


def test_run_skill_ok_exit_zero(melos_repo: Path, monkeypatch) -> None:
    skill_file = _write_skill(melos_repo, "deploy_staging_services")
    runner.invoke(
        app,
        ["skill", "--backend", "--path", str(skill_file.parent), "--deploy"],
    )

    captured: dict[str, object] = {}

    def fake_run(*args, **_kwargs) -> tuple[int, str]:
        captured["extra_argv"] = args[4] if len(args) > 4 else _kwargs.get("extra_argv")
        return 0, "Done.\nSKILL_OK: deployed\n"

    monkeypatch.setattr("kcia.commands.skill.run_cataloged_skill", fake_run)

    result = runner.invoke(app, ["skill", "--backend", "--deploy", "--", "production"])
    assert result.exit_code == 0, result.output
    assert captured["extra_argv"] == ["production"]
    assert "SKILL_OK" in result.output


def test_run_blocked_exit_two(melos_repo: Path, monkeypatch) -> None:
    skill_file = _write_skill(melos_repo, "deploy_staging_services")
    runner.invoke(
        app,
        ["skill", "--backend", "--path", str(skill_file.parent), "--deploy"],
    )

    monkeypatch.setattr(
        "kcia.commands.skill.run_cataloged_skill",
        lambda *args, **kwargs: (2, "BLOCKED: need credentials\n"),
    )

    result = runner.invoke(app, ["skill", "--backend", "--deploy"])
    assert result.exit_code == 2, result.output


def test_run_missing_skill_ok_exit_one(melos_repo: Path, monkeypatch) -> None:
    skill_file = _write_skill(melos_repo, "deploy_staging_services")
    runner.invoke(
        app,
        ["skill", "--backend", "--path", str(skill_file.parent), "--deploy"],
    )

    monkeypatch.setattr(
        "kcia.commands.skill.run_cataloged_skill",
        lambda *args, **kwargs: (1, "I talked but did not confirm.\n"),
    )

    result = runner.invoke(app, ["skill", "--backend", "--deploy"])
    assert result.exit_code == 1, result.output


def test_unique_shortcut_without_profile(melos_repo: Path, monkeypatch) -> None:
    _write_skill(melos_repo, "mote_kill")
    runner.invoke(app, ["skill", "--backend", "mote_kill"])

    calls: list[tuple[str, str]] = []

    def fake_run(repo, namespace, shortcut, *_args, **_kwargs):
        calls.append((namespace, shortcut))
        return 0, "SKILL_OK: ok\n"

    monkeypatch.setattr("kcia.commands.skill.run_cataloged_skill", fake_run)

    result = runner.invoke(app, ["skill", "--mote_kill"])
    assert result.exit_code == 0, result.output
    assert calls == [("backend", "mote_kill")]


def test_collision_without_profile_exits_one(melos_repo: Path) -> None:
    backend_skill = _write_skill(melos_repo, "shared_skill")
    mobile_skill = melos_repo / ".cursor" / "skills" / "shared_skill_mobile" / "SKILL.md"
    mobile_skill.parent.mkdir(parents=True)
    mobile_skill.write_text("# shared\n", encoding="utf-8")

    catalog = {
        "schema_version": 1,
        "commons": {},
        "profiles": {
            "backend": {
                "shared": {
                    "name": "shared_skill",
                    "path": ".cursor/skills/shared_skill/SKILL.md",
                }
            },
            "mobile": {
                "shared": {
                    "name": "shared_skill_mobile",
                    "path": ".cursor/skills/shared_skill_mobile/SKILL.md",
                }
            },
        },
    }
    skills_path(melos_repo).write_text(yaml.safe_dump(catalog), encoding="utf-8")

    with patch("kcia.commands.skill.run_cataloged_skill") as fake_run:
        result = runner.invoke(app, ["skill", "--shared"])
        assert result.exit_code == 1, result.output
        fake_run.assert_not_called()
    assert "multiple namespaces" in result.output.lower()


def test_lock_held_refuses_run(melos_repo: Path, monkeypatch) -> None:
    skill_file = _write_skill(melos_repo, "deploy_staging_services")
    runner.invoke(
        app,
        ["skill", "--backend", "--path", str(skill_file.parent), "--deploy"],
    )

    session = Session.create(melos_repo, text="task", mode="prompt")
    session.acquire_lock()

    with patch("kcia.commands.skill.run_cataloged_skill") as fake_run:
        result = runner.invoke(app, ["skill", "--backend", "--deploy"])
        assert result.exit_code == 1, result.output
        fake_run.assert_not_called()
    assert "Another wave is running" in result.output

    session.release_lock()
    if session_path(melos_repo).is_file():
        session_path(melos_repo).unlink()


def test_run_calls_builder_with_progress(melos_repo: Path, monkeypatch) -> None:
    skill_file = _write_skill(melos_repo, "deploy_staging_services")
    runner.invoke(
        app,
        ["skill", "--backend", "--path", str(skill_file.parent), "--deploy"],
    )

    captured: dict[str, object] = {}

    def fake_run(repo, namespace, shortcut, skill_path, extra_argv, **kwargs):
        captured["namespace"] = namespace
        captured["shortcut"] = shortcut
        captured["skill_path"] = skill_path
        captured["extra_argv"] = extra_argv
        return 0, "SKILL_OK: ok\n"

    monkeypatch.setattr("kcia.commands.skill.run_cataloged_skill", fake_run)
    monkeypatch.setattr("kcia.commands.skill.check_agents_ready", lambda _repo: [])

    result = runner.invoke(app, ["skill", "--backend", "--deploy", "staging"])
    assert result.exit_code == 0, result.output
    assert captured["namespace"] == "backend"
    assert captured["shortcut"] == "deploy"
    assert captured["extra_argv"] == ["staging"]
