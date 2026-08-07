"""Command resolution must match the repository's actual toolchain."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kcia.profiles.commands import resolve_commands
from kcia.profiles.inheritance import resolve_inheritance
from kcia.profiles.loader import load_registry

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def registry():
    return load_registry(ROOT)


def _commands(registry, profile_id: str, repo: Path) -> dict[str, str]:
    return resolve_commands(resolve_inheritance(profile_id, registry), None, repo, repo)


@pytest.mark.parametrize("profile_id", ["web-flutter", "mobile-flutter"])
def test_plain_flutter_without_a_pin(registry, tmp_path: Path, profile_id: str) -> None:
    """`fvm` used to be hardcoded, breaking validation wherever fvm is absent."""
    assert _commands(registry, profile_id, tmp_path)["test"] == "flutter test"


@pytest.mark.parametrize("profile_id", ["backend-dart"])
def test_plain_dart_without_a_pin(registry, tmp_path: Path, profile_id: str) -> None:
    assert _commands(registry, profile_id, tmp_path)["test"] == "dart test"


@pytest.mark.parametrize("marker", [".fvmrc", ".fvm/fvm_config.json"])
def test_fvm_is_used_when_the_repository_pins_a_version(
    registry, tmp_path: Path, marker: str
) -> None:
    target = tmp_path / marker
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"flutter": "3.35.7"}), encoding="utf-8")
    assert _commands(registry, "web-flutter", tmp_path)["test"] == "fvm flutter test"


def test_melos_workspace_without_fvm(registry, tmp_path: Path) -> None:
    (tmp_path / "melos.yaml").write_text("name: mono\n", encoding="utf-8")
    assert _commands(registry, "mobile-flutter", tmp_path)["test"] == "dart run melos run test:all"


def test_melos_workspace_with_fvm(registry, tmp_path: Path) -> None:
    """The most specific override must win, so ordering matters."""
    (tmp_path / "melos.yaml").write_text("name: mono\n", encoding="utf-8")
    (tmp_path / ".fvmrc").write_text('{"flutter": "3.35.7"}', encoding="utf-8")
    commands = _commands(registry, "mobile-flutter", tmp_path)
    assert commands["test"] == "fvm dart run melos run test:all"
    assert commands["verify"] == "fvm dart run melos run verify"


def test_no_profile_hardcodes_fvm_in_its_base_commands() -> None:
    import yaml

    for path in (ROOT / "control-plane" / "profiles").glob("*/profile.yaml"):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for name, command in (data.get("commands") or {}).items():
            assert not command.startswith("fvm "), f"{path.parent.name}.{name} hardcodes fvm"
