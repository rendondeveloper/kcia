"""Parse and validate the machine-readable execution block in `plan.md`.

The planner writes a fenced, parseable `execution:` section so the runner can
fan-out work by profile.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import yaml

from kcia.profiles.schema import Manifest


@dataclass(frozen=True)
class ProfileExecution:
    profile_id: str
    roots: list[str]
    summary: str | None = None
    depends_on: tuple[str, ...] = ()


class ExecutionBlockError(ValueError):
    pass


_FENCED_YAML_RE = re.compile(r"```yaml\s*([\s\S]*?)```", re.MULTILINE)


def parse_execution_block(plan_text: str) -> list[ProfileExecution]:
    """Extract `execution.profiles` from a fenced YAML block in the plan.

    Returns an empty list when no execution block exists.
    """
    if not plan_text.strip():
        return []

    for match in _FENCED_YAML_RE.finditer(plan_text):
        payload = (match.group(1) or "").strip()
        if not payload:
            continue
        try:
            data = yaml.safe_load(payload) or {}
        except yaml.YAMLError:
            continue

        execution = data.get("execution")
        if not isinstance(execution, dict):
            continue

        raw_profiles = execution.get("profiles") or []
        if not isinstance(raw_profiles, list):
            continue

        profiles: list[ProfileExecution] = []
        for item in raw_profiles:
            if not isinstance(item, dict):
                continue
            profile_id = item.get("id") or item.get("profile_id")
            if not isinstance(profile_id, str) or not profile_id.strip():
                continue
            roots = item.get("roots") or []
            if not isinstance(roots, list):
                raise ExecutionBlockError(
                    f"execution profiles[{profile_id!r}].roots must be a list"
                )
            roots_str = [str(r) for r in roots]
            summary = item.get("summary")
            raw_deps = item.get("depends_on") or []
            if not isinstance(raw_deps, list):
                raise ExecutionBlockError(
                    f"execution profiles[{profile_id!r}].depends_on must be a list"
                )
            depends_on = tuple(
                str(dep).strip()
                for dep in raw_deps
                if isinstance(dep, str) and dep.strip()
            )
            profiles.append(
                ProfileExecution(
                    profile_id=profile_id.strip(),
                    roots=roots_str,
                    summary=str(summary) if summary is not None else None,
                    depends_on=depends_on,
                )
            )

        return profiles

    return []


def validate_execution_against_manifest(
    executions: list[ProfileExecution], manifest: Manifest
) -> None:
    """Validate that profile ids and roots belong to the manifest.

    The check is conservative: we require every declared `roots` string to be a
    direct element of the manifest `roots` list.
    """
    manifest_map = {entry.id: entry for entry in manifest.profiles}

    for exec_entry in executions:
        if exec_entry.profile_id not in manifest_map:
            raise ExecutionBlockError(
                f"Unknown profile id in execution block: {exec_entry.profile_id!r}"
            )
        manifest_entry = manifest_map[exec_entry.profile_id]
        missing = [r for r in exec_entry.roots if r not in manifest_entry.roots]
        if missing:
            raise ExecutionBlockError(
                f"Execution roots for {exec_entry.profile_id!r} are not a subset "
                f"of the manifest roots. Missing: {missing!r}"
            )


def _root_prefix(pattern: str) -> str | None:
    pattern = pattern.strip()
    if pattern in {".", "**"}:
        # Root spans whole repo: overlapping is impossible to prove disjoint.
        return None
    if pattern.endswith("/**"):
        return pattern[: -len("/**")]
    return None


def _prefixes_overlap(a: str, b: str) -> bool:
    # Prefix overlap by directory nesting.
    a = a.rstrip("/")
    b = b.rstrip("/")
    if a == b:
        return True
    return a.startswith(b + "/") or b.startswith(a + "/")


def validate_disjoint_roots(executions: list[ProfileExecution]) -> None:
    """Raise when two profile executions could overlap in edit access.

    This is intentionally strict and only supports common manifest patterns
    ending with `/**` (or `.` / `**` which are treated as whole-repo).
    """
    roots_by_profile: dict[str, list[str]] = {}
    for exec_entry in executions:
        prefixes: list[str] = []
        for root in exec_entry.roots:
            prefix = _root_prefix(root)
            if prefix is None:
                raise ExecutionBlockError(
                    f"Cannot validate disjoint roots for {exec_entry.profile_id!r}: "
                    f"root {root!r} is not a simple '<dir>/**' pattern."
                )
            prefixes.append(prefix)
        roots_by_profile[exec_entry.profile_id] = prefixes

    profile_ids = list(roots_by_profile.keys())
    for i in range(len(profile_ids)):
        for j in range(i + 1, len(profile_ids)):
            a = profile_ids[i]
            b = profile_ids[j]
            for ra in roots_by_profile[a]:
                for rb in roots_by_profile[b]:
                    if _prefixes_overlap(ra, rb):
                        raise ExecutionBlockError(
                            f"Overlapping execution roots between {a!r} and {b!r}: "
                            f"{ra!r} overlaps {rb!r}"
                        )


def validate_execution_dependencies(executions: list[ProfileExecution]) -> None:
    """Raise when a dependency references an unknown profile or forms a cycle."""
    profile_ids = {entry.profile_id for entry in executions}
    for entry in executions:
        for dep in entry.depends_on:
            if dep not in profile_ids:
                raise ExecutionBlockError(
                    f"Unknown dependency {dep!r} in execution profile "
                    f"{entry.profile_id!r}"
                )

    graph = {entry.profile_id: list(entry.depends_on) for entry in executions}
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> None:
        if node in visiting:
            raise ExecutionBlockError(
                f"Cyclic dependency in execution block involving {node!r}"
            )
        if node in visited:
            return
        visiting.add(node)
        for dep in graph.get(node, []):
            visit(dep)
        visiting.remove(node)
        visited.add(node)

    for profile_id in graph:
        visit(profile_id)


def execution_batches(
    executions: list[ProfileExecution],
) -> list[list[ProfileExecution]]:
    """Group profile executions into ordered batches by dependency depth."""
    remaining = {entry.profile_id: entry for entry in executions}
    satisfied: set[str] = set()
    batches: list[list[ProfileExecution]] = []

    while remaining:
        ready = [
            entry
            for entry in remaining.values()
            if all(dep in satisfied for dep in entry.depends_on)
        ]
        if not ready:
            raise ExecutionBlockError("Cyclic dependency in execution block")
        batches.append(ready)
        for entry in ready:
            satisfied.add(entry.profile_id)
            del remaining[entry.profile_id]

    return batches


_INTEGRATION_CHECKLIST_RE = re.compile(
    r"^##\s+Integration checklist\s*$([\s\S]*?)(?=^##\s+|\Z)",
    re.MULTILINE | re.IGNORECASE,
)


def parse_integration_checklist(plan_text: str) -> str | None:
    """Return the body of the Integration checklist section in plan.md, if any."""
    if not plan_text.strip():
        return None
    match = _INTEGRATION_CHECKLIST_RE.search(plan_text)
    if not match:
        return None
    body = (match.group(1) or "").strip()
    return body or None

