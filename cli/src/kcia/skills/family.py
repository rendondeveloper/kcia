"""Map profile ids to catalog namespace families."""

from __future__ import annotations

from pathlib import Path

from kcia.waves.session import load_manifest


def profile_family(profile_id: str) -> str:
    """First segment of a concrete profile id (`backend-dart` → `backend`)."""
    return profile_id.split("-", 1)[0]


def manifest_families(repo_root: Path) -> frozenset[str]:
    """Profile families declared in the repository manifest."""
    manifest = load_manifest(repo_root)
    if manifest is None:
        return frozenset()
    return frozenset(profile_family(entry.id) for entry in manifest.profiles)


def namespace_available(repo_root: Path, namespace: str) -> bool:
    """Whether a profile namespace is declared in this repository."""
    if namespace == "commons":
        return True
    return namespace in manifest_families(repo_root)
