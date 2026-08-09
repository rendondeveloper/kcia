"""Precomputed repository map for wave prompts."""

from __future__ import annotations

from pathlib import Path

from kcia.profiles.commands import resolve_commands
from kcia.profiles.inheritance import ProfileRegistry, resolve_inheritance
from kcia.profiles.loader import load_registry
from kcia.profiles.schema import Manifest


def build_repo_map(manifest: Manifest, registry: ProfileRegistry, repo_root: Path) -> str:
    """Markdown determinista. Cadena vacía si el manifest no tiene profiles."""
    if not manifest.profiles:
        return ""

    packages = _package_rows(manifest, registry, repo_root)
    if not packages:
        return ""

    layout = manifest.project.get("layout", "single")
    lines = [
        "## Repository map",
        "",
        f"Layout: {layout}. Detected {len(packages)} packages.",
        "",
        "| Path | Profile | Test | Lint |",
        "|---|---|---|---|",
    ]
    for row in packages:
        lines.append(
            f"| {row['path']} | {row['profile']} | `{row['test']}` | `{row['lint']}` |"
        )

    shared = _shared_packages(manifest)
    if shared:
        lines.append("")
        lines.append(shared)

    lines.append("")
    return "\n".join(lines)


def _package_rows(
    manifest: Manifest, registry: ProfileRegistry, repo_root: Path
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for entry in manifest.profiles:
        if entry.id not in registry.profiles:
            continue
        loaded = registry.profiles[entry.id]
        resolved = resolve_inheritance(entry.id, registry)
        for root in entry.roots:
            package_path = root[:-3] if root.endswith("/**") else root.rstrip("/")
            if package_path in {"."}:
                package_path = "."
            if package_path == "**":
                package_path = "."
            if package_path in seen:
                continue
            seen.add(package_path)
            cwd = repo_root if package_path == "." else repo_root / package_path
            commands = resolve_commands(resolved, entry, loaded.root, cwd)
            rows.append(
                {
                    "path": "." if package_path == "." else package_path,
                    "profile": entry.id,
                    "test": commands.get("test", "—"),
                    "lint": commands.get("lint", "—"),
                }
            )
    rows.sort(key=lambda row: row["path"])
    return rows


def _shared_packages(manifest: Manifest) -> str:
    if not manifest.dependencies:
        return ""
    parts: list[str] = []
    for dep in manifest.dependencies:
        source = dep.get("source", "")
        consumers = dep.get("triggers_validation_of") or dep.get("used_by") or []
        if source and consumers:
            parts.append(f"{source} (used by {', '.join(consumers)})")
    if not parts:
        return ""
    return f"Shared packages consumed by others: {', '.join(parts)}."
