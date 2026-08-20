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
            profiles.append(
                ProfileExecution(
                    profile_id=profile_id.strip(),
                    roots=roots_str,
                    summary=str(summary) if summary is not None else None,
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

