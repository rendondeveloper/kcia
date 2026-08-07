"""Repository profile detection."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from kcia.profiles.inheritance import ProfileRegistry
from kcia.profiles.predicates import _ReadCache, evaluate

EXCLUDED_DIRS = {
    ".git",
    ".dart_tool",
    ".fvm",
    "build",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    "dist",
    "target",
    ".gradle",
    "Pods",
    ".idea",
    ".vscode",
    ".ai",
}

CONFIDENCE_ORDER = {"high": 3, "medium": 2, "low": 1}


@dataclass(frozen=True)
class DetectionHit:
    profile_id: str
    root: Path
    confidence: str
    evidence: list[str]
    rule_index: int


def find_candidate_dirs(repo_root: Path, *, extra_exclude: list[str] | None = None) -> list[Path]:
    repo_root = repo_root.resolve()
    excluded = set(EXCLUDED_DIRS)
    if extra_exclude:
        excluded.update(extra_exclude)

    candidates: list[Path] = []

    melos_path = repo_root / "melos.yaml"
    if melos_path.is_file():
        candidates.extend(_melos_package_dirs(repo_root, melos_path))
    else:
        pnpm_workspace = repo_root / "pnpm-workspace.yaml"
        if pnpm_workspace.is_file():
            candidates.extend(_pnpm_workspace_dirs(repo_root, pnpm_workspace))

        package_json = repo_root / "package.json"
        if package_json.is_file():
            candidates.extend(_npm_workspace_dirs(repo_root, package_json))

        if not candidates:
            candidates.extend(_walk_candidate_dirs(repo_root, excluded))

    if repo_root not in candidates:
        candidates.insert(0, repo_root)

    deduped: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved not in seen:
            seen.add(resolved)
            deduped.append(resolved)
    return deduped


def detect(
    repo_root: Path,
    registry: ProfileRegistry,
    *,
    extra_exclude: list[str] | None = None,
) -> list[DetectionHit]:
    repo_root = repo_root.resolve()
    hits: list[DetectionHit] = []
    cache = _ReadCache()
    candidates = find_candidate_dirs(repo_root, extra_exclude=extra_exclude)

    for candidate in candidates:
        rel_root = candidate.relative_to(repo_root) if candidate != repo_root else Path(".")
        for profile_id, loaded in registry.profiles.items():
            if loaded.spec.abstract:
                continue
            for index, rule in enumerate(loaded.spec.detect):
                if evaluate(
                    rule.when,
                    candidate,
                    profile_id=profile_id,
                    predicate_path=f"detect[{index}].when",
                    cache=cache,
                ):
                    hits.append(
                        DetectionHit(
                            profile_id=profile_id,
                            root=rel_root,
                            confidence=rule.confidence,
                            evidence=[rule.evidence],
                            rule_index=index,
                        )
                    )

    hits.sort(
        key=lambda hit: (
            str(hit.root),
            -CONFIDENCE_ORDER.get(hit.confidence, 0),
            hit.profile_id,
        )
    )
    return hits


def _melos_package_dirs(repo_root: Path, melos_path: Path) -> list[Path]:
    data = yaml.safe_load(melos_path.read_text(encoding="utf-8")) or {}
    packages = data.get("packages") or data.get("workspace", {}).get("packages") or []
    return _expand_globs(repo_root, packages)


def _pnpm_workspace_dirs(repo_root: Path, workspace_path: Path) -> list[Path]:
    data = yaml.safe_load(workspace_path.read_text(encoding="utf-8")) or {}
    packages = data.get("packages", [])
    return _expand_globs(repo_root, packages)


def _npm_workspace_dirs(repo_root: Path, package_json: Path) -> list[Path]:
    import json

    data = json.loads(package_json.read_text(encoding="utf-8"))
    workspaces = data.get("workspaces", [])
    if isinstance(workspaces, dict):
        packages = workspaces.get("packages", [])
    else:
        packages = workspaces
    return _expand_globs(repo_root, packages)


def _expand_globs(repo_root: Path, patterns: list[str]) -> list[Path]:
    results: list[Path] = []
    for pattern in patterns:
        if pattern.endswith("/**"):
            pattern = pattern[:-3]
        for match in repo_root.glob(pattern):
            if match.is_dir():
                results.append(match)
    return results


def _walk_candidate_dirs(repo_root: Path, excluded: set[str], depth: int = 0, max_depth: int = 4) -> list[Path]:
    if depth > max_depth:
        return []
    results: list[Path] = []
    for child in sorted(repo_root.iterdir()):
        if not child.is_dir() or child.name in excluded:
            continue
        if _looks_like_project_root(child):
            results.append(child)
        results.extend(_walk_candidate_dirs(child, excluded, depth + 1, max_depth))
    return results


def _looks_like_project_root(path: Path) -> bool:
    markers = [
        "pubspec.yaml",
        "package.json",
        "pyproject.toml",
        "go.mod",
        "build.gradle.kts",
        "Cargo.toml",
    ]
    return any((path / marker).is_file() for marker in markers)
