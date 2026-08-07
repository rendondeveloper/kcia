"""Resolved profile command lookup."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from kcia.profiles.inheritance import ResolvedProfile
from kcia.profiles.predicates import _ReadCache, evaluate
from kcia.profiles.schema import ProfileManifestEntry


def resolve_commands(
    resolved: ResolvedProfile,
    manifest_entry: ProfileManifestEntry | None,
    profile_root: Path,
    cwd: Path,
) -> dict[str, str]:
    commands = dict(resolved.commands)
    cache = _ReadCache()
    for override in resolved.command_overrides:
        when = override.get("when", {})
        if evaluate(when, cwd, cache=cache):
            commands.update(override.get("commands", {}))
    if manifest_entry is not None:
        commands.update(manifest_entry.commands)
    return commands
