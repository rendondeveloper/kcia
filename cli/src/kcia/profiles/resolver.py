"""Path to active profile resolution."""

from __future__ import annotations

from pathlib import Path

import pathspec

from kcia.profiles.schema import Manifest


def resolve_for_path(path: Path, manifest: Manifest, repo_root: Path | None = None) -> list[str]:
    repo_root = (repo_root or path).resolve()
    if path.is_file():
        rel_path = path.resolve().relative_to(repo_root)
    else:
        rel_path = path.resolve().relative_to(repo_root)
    rel_text = rel_path.as_posix()

    matched: list[str] = []
    for entry in manifest.profiles:
        if _matches_any_root(rel_text, entry.roots):
            matched.append(entry.id)

    if matched:
        return _stable_order(matched, manifest)

    default_profile = manifest.default_profile
    if default_profile:
        return [default_profile]
    return []


def resolve_for_task(paths: list[Path], manifest: Manifest, repo_root: Path) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for path in paths:
        for profile_id in resolve_for_path(path, manifest, repo_root=repo_root):
            if profile_id not in seen:
                seen.add(profile_id)
                ordered.append(profile_id)
    return _stable_order(ordered, manifest)


def resolve_for_cwd(cwd: Path, manifest: Manifest, repo_root: Path) -> list[str]:
    return resolve_for_path(cwd, manifest, repo_root=repo_root)


def _matches_any_root(rel_text: str, roots: list[str]) -> bool:
    for root in roots:
        pattern = root
        if pattern.endswith("/**"):
            prefix = pattern[:-3]
            if rel_text == prefix or rel_text.startswith(prefix + "/"):
                return True
            continue
        spec = pathspec.PathSpec.from_lines("gitwildmatch", [pattern])
        if spec.match_file(rel_text):
            return True
    return False


def _stable_order(profile_ids: list[str], manifest: Manifest) -> list[str]:
    manifest_order = [entry.id for entry in manifest.profiles]
    order_index = {profile_id: index for index, profile_id in enumerate(manifest_order)}
    return sorted(profile_ids, key=lambda profile_id: order_index.get(profile_id, 10_000))
