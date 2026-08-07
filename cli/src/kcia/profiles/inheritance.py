"""Profile inheritance resolution."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from kcia.profiles.schema import AdapterConfig, ProfileSpec, ValidationConfig


class CircularInheritanceError(ValueError):
    """Profile inheritance cycle detected."""


class InheritanceTooDeepError(ValueError):
    """Profile inheritance exceeds maximum depth."""


class UnknownParentError(ValueError):
    """Parent profile does not exist."""


MAX_INHERITANCE_DEPTH = 3


@dataclass
class ResolvedProfile:
    id: str
    display_name: str
    description: str
    abstract: bool
    commands: dict[str, str]
    command_overrides: list[dict[str, Any]]
    references: list[tuple[str, Path]]
    workflows: dict[str, Path]
    rules: dict[str, Any]
    adapters: dict[str, Any]
    validation: dict[str, Any]
    detect: list[Any]
    ancestors: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class LoadedProfile:
    spec: ProfileSpec
    root: Path
    source_kind: str
    pack_name: str


@dataclass(frozen=True)
class ProfileRegistry:
    profiles: dict[str, LoadedProfile]
    sources: dict[str, str]
    shadowed: list[tuple[str, str]]


def resolve_inheritance(profile_id: str, registry: ProfileRegistry) -> ResolvedProfile:
    if profile_id not in registry.profiles:
        raise UnknownParentError(
            f"unknown profile '{profile_id}'; available: {sorted(registry.profiles)}"
        )
    chain = _build_chain(profile_id, registry)
    merged = _merge_chain(chain, registry)
    return merged


def _build_chain(profile_id: str, registry: ProfileRegistry) -> list[str]:
    chain: list[str] = []
    current = profile_id
    seen: set[str] = set()
    while current:
        if current in seen:
            raise CircularInheritanceError(
                f"circular inheritance: {' -> '.join([*chain, current])}"
            )
        if len(chain) >= MAX_INHERITANCE_DEPTH:
            raise InheritanceTooDeepError(
                f"inheritance depth exceeds {MAX_INHERITANCE_DEPTH} for '{profile_id}'"
            )
        if current not in registry.profiles:
            raise UnknownParentError(
                f"unknown parent '{current}' while resolving '{profile_id}'; "
                f"available: {sorted(registry.profiles)}"
            )
        seen.add(current)
        chain.append(current)
        parent = registry.profiles[current].spec.extends
        current = parent or ""
    chain.reverse()
    return chain


def _merge_chain(chain: list[str], registry: ProfileRegistry) -> ResolvedProfile:
    commands: dict[str, str] = {}
    rules: dict[str, Any] = {}
    references: list[tuple[str, Path]] = []
    workflows: dict[str, Path] = {}
    adapters: dict[str, Any] = {}
    validation: dict[str, Any] = {}
    command_overrides: list[dict[str, Any]] = []
    leaf = registry.profiles[chain[-1]].spec

    for profile_id in chain:
        spec = registry.profiles[profile_id].spec
        root = registry.profiles[profile_id].root
        commands.update(spec.commands)
        rules.update(spec.rules)
        for reference in spec.references:
            references.append((profile_id, root / reference))
        for workflow in spec.workflows:
            workflows[Path(workflow).name] = root / workflow
        adapters = _deep_merge(adapters, spec.adapters.model_dump(exclude_none=True))
        validation.update(spec.validation.model_dump())
        command_overrides.extend(
            {"when": item.when, "commands": item.commands} for item in spec.command_overrides
        )

    return ResolvedProfile(
        id=leaf.id,
        display_name=leaf.display_name,
        description=leaf.description,
        abstract=leaf.abstract,
        commands=commands,
        command_overrides=command_overrides,
        references=references,
        workflows=workflows,
        rules=rules,
        adapters=adapters,
        validation=validation,
        detect=list(leaf.detect),
        ancestors=chain[:-1],
    )


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result
