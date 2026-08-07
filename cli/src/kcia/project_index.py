"""Deterministic project facts for `.ai/context/project.md`.

Only what Python can verify: manifest fields, entry points, source directories.
Interpretation — what the domain is, what each file does — is deliberately left
out: it ages badly, nobody updates it, and the agent reads the code anyway.

The output is capped so this block stays a small, cache-friendly prefix that is
injected into every wave.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

MAX_TOKENS = 400
MAX_DEPENDENCIES = 12
MAX_SOURCE_DIRS = 12
MAX_MANIFEST_BYTES = 256_000

# Ubiquitous, tells the reader nothing about this specific project.
_UNINTERESTING_DEPS = {"flutter", "flutter_test", "flutter_lints", "cupertino_icons"}

_PLATFORM_DIRS = ("android", "ios", "linux", "macos", "web", "windows")


def build_project_facts(repo_root: Path, *, name: str, layout: str) -> str:
    """Markdown body describing the project, or '' when nothing is derivable."""
    sections: list[str] = []

    stack = _stack_line(repo_root, name=name, layout=layout)
    if stack:
        sections.append(f"## Summary\n{stack}")

    dependencies = _dependencies(repo_root)
    if dependencies:
        shown = ", ".join(dependencies[:MAX_DEPENDENCIES])
        if len(dependencies) > MAX_DEPENDENCIES:
            shown += f", … (+{len(dependencies) - MAX_DEPENDENCIES})"
        sections.append(f"## Key dependencies\n{shown}")

    layout_block = _source_layout(repo_root)
    if layout_block:
        sections.append(f"## Source layout\n{layout_block}")

    return _cap("\n\n".join(sections))


def _cap(text: str) -> str:
    """Trim whole sections until the block fits the budget."""
    while text and _estimate_tokens(text) > MAX_TOKENS:
        blocks = text.split("\n\n")
        if len(blocks) == 1:
            break
        text = "\n\n".join(blocks[:-1])
    return text


def _estimate_tokens(text: str) -> int:
    return len(text) // 4


def _stack_line(repo_root: Path, *, name: str, layout: str) -> str:
    # A workspace root holds no manifest of its own; describe it from its members.
    workspace = _workspace_line(repo_root, name=name)
    if workspace:
        return workspace

    pubspec = _read_yaml(repo_root / "pubspec.yaml")
    package_json = _read_json(repo_root / "package.json")
    pyproject = repo_root / "pyproject.toml"

    parts: list[str] = []
    if pubspec:
        sdk = (pubspec.get("environment") or {}).get("sdk")
        flutter = "flutter" in (pubspec.get("dependencies") or {})
        parts.append(f"{'Flutter' if flutter else 'Dart'} project")
        if sdk:
            parts.append(f"Dart SDK {sdk}")
    elif package_json:
        parts.append("Node/JavaScript project")
        engines = (package_json.get("engines") or {}).get("node")
        if engines:
            parts.append(f"Node {engines}")
    elif pyproject.is_file():
        parts.append("Python project")

    if not parts:
        return ""

    platforms = [d for d in _PLATFORM_DIRS if (repo_root / d).is_dir()]
    line = f"{name} — {layout} {', '.join(parts)}."
    if platforms:
        line += f"\nPlatforms: {', '.join(platforms)}."
    return line


def _workspace_line(repo_root: Path, *, name: str) -> str:
    """Describe a monorepo root, which has members instead of a manifest."""
    melos = _read_yaml(repo_root / "melos.yaml")
    if melos is not None:
        kind = "Melos (Dart/Flutter) workspace"
    elif (repo_root / "pnpm-workspace.yaml").is_file():
        kind = "pnpm workspace"
    else:
        package_json = _read_json(repo_root / "package.json")
        if not package_json or not package_json.get("workspaces"):
            return ""
        kind = "npm workspace"

    members = sorted(
        child.name
        for child in (repo_root / "packages").iterdir()
        if child.is_dir() and not child.name.startswith(".")
    ) if (repo_root / "packages").is_dir() else []

    line = f"{name} — {kind}."
    if members:
        shown = members[:MAX_SOURCE_DIRS]
        suffix = f", … (+{len(members) - len(shown)})" if len(members) > len(shown) else ""
        line += f"\nPackages: {', '.join(shown)}{suffix}."
    return line


def _dependencies(repo_root: Path) -> list[str]:
    pubspec = _read_yaml(repo_root / "pubspec.yaml")
    if pubspec:
        raw = pubspec.get("dependencies") or {}
        return [key for key in raw if key not in _UNINTERESTING_DEPS]

    package_json = _read_json(repo_root / "package.json")
    if package_json:
        return list((package_json.get("dependencies") or {}).keys())
    return []


def _source_layout(repo_root: Path) -> str:
    lines: list[str] = []

    for entry_point in ("lib/main.dart", "src/index.ts", "src/main.py", "main.go"):
        if (repo_root / entry_point).is_file():
            lines.append(f"- `{entry_point}` — entry point")
            break

    for source_root in ("lib", "src", "app"):
        root = repo_root / source_root
        if not root.is_dir():
            continue
        children = sorted(
            child.name
            for child in root.iterdir()
            if child.is_dir() and not child.name.startswith(".")
        )
        if children:
            shown = children[:MAX_SOURCE_DIRS]
            suffix = f", … (+{len(children) - len(shown)})" if len(children) > len(shown) else ""
            lines.append(f"- `{source_root}/`: {', '.join(shown)}{suffix}")
        else:
            lines.append(f"- `{source_root}/`")
        break

    for test_root in ("test", "tests"):
        if (repo_root / test_root).is_dir():
            lines.append(f"- `{test_root}/` — tests")
            break

    return "\n".join(lines)


def _read_yaml(path: Path) -> dict[str, Any] | None:
    text = _read_text(path)
    if text is None:
        return None
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return None
    return data if isinstance(data, dict) else None


def _read_json(path: Path) -> dict[str, Any] | None:
    text = _read_text(path)
    if text is None:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _read_text(path: Path) -> str | None:
    try:
        if not path.is_file() or path.stat().st_size > MAX_MANIFEST_BYTES:
            return None
        return path.read_text(encoding="utf-8")
    except OSError:
        return None
