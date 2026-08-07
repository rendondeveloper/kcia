"""Profile system tests — Fase 1 acceptance criteria."""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

import pytest

from kcia.profiles.detector import detect, find_candidate_dirs
from kcia.profiles.inheritance import (
    CircularInheritanceError,
    LoadedProfile,
    ProfileRegistry,
    ReferenceEntry,
    resolve_inheritance,
)
from kcia.profiles.loader import load_registry
from kcia.profiles.schema import ProfileSpec, ReferenceSpec

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
REPOS = FIXTURES / "repos"
NODEPACK = FIXTURES / "packs" / "nodepack"
KCIA = ROOT / ".venv" / "bin" / "kcia"


def _load_builtin_registry() -> ProfileRegistry:
    return load_registry(None)


def _detect_in(repo_name: str) -> list[tuple[str, str, str]]:
    repo = REPOS / repo_name
    registry = _load_builtin_registry()
    hits = detect(repo, registry)
    return [(h.profile_id, str(h.root), h.confidence) for h in hits]


def test_profile_list_shows_three_concrete_profiles() -> None:
    result = subprocess.run(
        [str(KCIA), "profile", "list"],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert result.returncode == 0
    lines = [line for line in result.stdout.strip().splitlines() if line]
    ids = {line.split("\t")[0] for line in lines}
    assert ids == {"backend-dart", "mobile-flutter", "web-flutter"}


def test_profile_list_all_shows_four_including_abstract() -> None:
    result = subprocess.run(
        [str(KCIA), "profile", "list", "--all"],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert result.returncode == 0
    lines = [line for line in result.stdout.strip().splitlines() if line]
    ids = {line.split("\t")[0] for line in lines}
    assert ids == {"_dart-core", "backend-dart", "mobile-flutter", "web-flutter"}


def test_profile_show_inherits_dart_core_rules() -> None:
    result = subprocess.run(
        [str(KCIA), "profile", "show", "mobile-flutter"],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert result.returncode == 0
    assert "require_freezed_over_equatable: True" in result.stdout
    assert "extends: _dart-core -> mobile-flutter" in result.stdout


def test_detect_flutter_mobile() -> None:
    hits = _detect_in("flutter_mobile")
    assert ("mobile-flutter", ".", "high") in hits


def test_detect_flutter_web() -> None:
    hits = _detect_in("flutter_web")
    assert ("web-flutter", ".", "high") in hits


def test_detect_dart_server() -> None:
    hits = _detect_in("dart_server")
    profile_ids = {h[0] for h in hits}
    assert "backend-dart" in profile_ids
    high = [h for h in hits if h[0] == "backend-dart" and h[2] == "high"]
    assert high


def test_detect_flutter_universal_two_high_hits() -> None:
    hits = _detect_in("flutter_universal")
    high_hits = [h for h in hits if h[2] == "high"]
    profile_ids = {h[0] for h in high_hits}
    assert profile_ids == {"mobile-flutter", "web-flutter"}


def test_detect_melos_mono_uses_melos_candidates() -> None:
    repo = REPOS / "melos_mono"
    candidates = find_candidate_dirs(repo)
    candidate_names = {str(c.relative_to(repo)) for c in candidates}
    assert "packages/app_mobile" in candidate_names
    assert "packages/app_web" in candidate_names
    assert "packages/api" in candidate_names
    assert "packages/shared" in candidate_names

    hits = detect(repo, _load_builtin_registry())
    roots = {str(h.root) for h in hits}
    assert "packages/app_mobile" in roots
    assert "packages/app_web" in roots
    assert "packages/api" in roots


def test_detect_empty_repo_no_hits() -> None:
    hits = _detect_in("empty")
    assert hits == []


def test_nodepack_extensibility_without_python_changes() -> None:
    repo = REPOS / "react_app"
    previous = os.environ.get("KCIA_PROFILE_PATH")
    os.environ["KCIA_PROFILE_PATH"] = str(NODEPACK)
    try:
        registry = load_registry(None)
        assert "react-app" in registry.profiles
        hits = detect(repo, registry)
        assert any(h.profile_id == "react-app" and h.confidence == "high" for h in hits)
    finally:
        if previous is None:
            os.environ.pop("KCIA_PROFILE_PATH", None)
        else:
            os.environ["KCIA_PROFILE_PATH"] = previous


def test_scaffold_generates_valid_profile() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        result = subprocess.run(
            [str(KCIA), "profile", "scaffold", "my-thing", "--extends", "_dart-core"],
            capture_output=True,
            text=True,
            cwd=tmp,
        )
        assert result.returncode == 0, result.stderr
        pack_dir = Path(tmp) / "my-thing"
        validate = subprocess.run(
            [str(KCIA), "profile", "validate", str(pack_dir)],
            capture_output=True,
            text=True,
            cwd=ROOT,
        )
        assert validate.returncode == 0, validate.stderr + validate.stdout


def test_circular_inheritance_raises() -> None:
    spec_a = ProfileSpec.model_validate(
        {
            "schema_version": 2,
            "id": "profile-a",
            "display_name": "A",
            "extends": "profile-b",
        }
    )
    spec_b = ProfileSpec.model_validate(
        {
            "schema_version": 2,
            "id": "profile-b",
            "display_name": "B",
            "extends": "profile-a",
        }
    )
    root = Path("/tmp")
    registry = ProfileRegistry(
        profiles={
            "profile-a": LoadedProfile(spec_a, root, "test", "test"),
            "profile-b": LoadedProfile(spec_b, root, "test", "test"),
        },
        sources={},
        shadowed=[],
    )
    with pytest.raises(CircularInheritanceError, match="circular inheritance"):
        resolve_inheritance("profile-a", registry)


def test_reference_spec_normalizes_string_and_object_forms() -> None:
    spec = ProfileSpec.model_validate(
        {
            "schema_version": 2,
            "id": "demo",
            "display_name": "Demo",
            "references": [
                "references/coding.md",
                {"path": "references/api.md", "tags": ["api", "architecture"]},
            ],
        }
    )
    assert spec.references[0] == ReferenceSpec(path="references/coding.md", tags=["coding"])
    assert spec.references[1].tags == ["api", "architecture"]


def test_resolve_inheritance_propagates_reference_tags() -> None:
    parent = ProfileSpec.model_validate(
        {
            "schema_version": 2,
            "id": "parent",
            "display_name": "Parent",
            "references": [{"path": "references/coding.md", "tags": ["coding"]}],
        }
    )
    child = ProfileSpec.model_validate(
        {
            "schema_version": 2,
            "id": "child",
            "display_name": "Child",
            "extends": "parent",
            "references": [{"path": "references/api.md", "tags": ["api"]}],
        }
    )
    root = Path("/tmp/profiles")
    registry = ProfileRegistry(
        profiles={
            "parent": LoadedProfile(parent, root / "parent", "test", "test"),
            "child": LoadedProfile(child, root / "child", "test", "test"),
        },
        sources={},
        shadowed=[],
    )
    resolved = resolve_inheritance("child", registry)
    assert resolved.references == [
        ReferenceEntry("parent", root / "parent" / "references/coding.md", ("coding",)),
        ReferenceEntry("child", root / "child" / "references/api.md", ("api",)),
    ]


def test_nodepack_string_reference_derives_stem_tag() -> None:
    previous = os.environ.get("KCIA_PROFILE_PATH")
    os.environ["KCIA_PROFILE_PATH"] = str(NODEPACK)
    try:
        registry = load_registry(None)
        resolved = resolve_inheritance("react-app", registry)
        assert resolved.references[0].tags == ("coding",)
    finally:
        if previous is None:
            os.environ.pop("KCIA_PROFILE_PATH", None)
        else:
            os.environ["KCIA_PROFILE_PATH"] = previous
