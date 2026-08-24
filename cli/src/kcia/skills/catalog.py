"""Load, save, and query `.ai/skills.yaml`."""

from __future__ import annotations

from pathlib import Path

import yaml

from kcia.skills.constants import RESERVED_FLAGS, SHORTCUT_PATTERN, SKILLS_REL_PATH
from kcia.skills.schema import SkillEntry, SkillsCatalog


def skills_path(repo_root: Path) -> Path:
    return repo_root / SKILLS_REL_PATH


def load_catalog(repo_root: Path) -> SkillsCatalog:
    path = skills_path(repo_root)
    if not path.is_file():
        return SkillsCatalog()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return SkillsCatalog.model_validate(data)


def save_catalog(repo_root: Path, catalog: SkillsCatalog) -> Path:
    path = skills_path(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = catalog.model_dump(mode="python")
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return path


def store_path(repo_root: Path, skill_path: Path) -> str:
    """Persist a repo-relative path when possible."""
    resolved = skill_path.resolve()
    try:
        return resolved.relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return str(resolved)


def is_valid_shortcut(shortcut: str) -> bool:
    return SHORTCUT_PATTERN.match(shortcut) is not None and shortcut not in RESERVED_FLAGS


def namespace_entries(catalog: SkillsCatalog, namespace: str) -> dict[str, SkillEntry]:
    if namespace == "commons":
        return catalog.commons
    return catalog.profiles.get(namespace, {})


def find_shortcut_namespaces(
    catalog: SkillsCatalog, shortcut: str
) -> list[str]:
    hits: list[str] = []
    if shortcut in catalog.commons:
        hits.append("commons")
    for namespace, entries in catalog.profiles.items():
        if shortcut in entries:
            hits.append(namespace)
    return hits


def get_entry(
    catalog: SkillsCatalog, namespace: str, shortcut: str
) -> SkillEntry | None:
    if namespace == "commons":
        return catalog.commons.get(shortcut)
    return catalog.profiles.get(namespace, {}).get(shortcut)


def set_entry(
    catalog: SkillsCatalog, namespace: str, shortcut: str, entry: SkillEntry
) -> None:
    if namespace == "commons":
        catalog.commons[shortcut] = entry
        return
    catalog.profiles.setdefault(namespace, {})[shortcut] = entry


def remove_entry(catalog: SkillsCatalog, namespace: str, shortcut: str) -> bool:
    if namespace == "commons":
        if shortcut not in catalog.commons:
            return False
        del catalog.commons[shortcut]
        return True
    entries = catalog.profiles.get(namespace)
    if entries is None or shortcut not in entries:
        return False
    del entries[shortcut]
    if not entries:
        catalog.profiles.pop(namespace, None)
    return True


def namespaces_with_entries(catalog: SkillsCatalog) -> list[str]:
    namespaces: list[str] = []
    if catalog.commons:
        namespaces.append("commons")
    for namespace in sorted(catalog.profiles):
        if catalog.profiles[namespace]:
            namespaces.append(namespace)
    return namespaces
