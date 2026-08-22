"""Provider adapter registry."""

from __future__ import annotations

import shutil
from importlib.metadata import entry_points
from typing import Any

from kcia.providers.base import ProviderAdapter
from kcia.providers.catalog import ProviderCatalogEntry, load_catalog
from kcia.providers.claude import ClaudeAdapter
from kcia.providers.cursor import CursorAdapter
from kcia.providers.opencode import OpenCodeAdapter

AGENT_ROLES = ("planner", "builder")

_BUILTIN_ADAPTERS: dict[str, type] = {
    "claude": ClaudeAdapter,
    "cursor": CursorAdapter,
    "opencode": OpenCodeAdapter,
}


def get_adapter(provider_id: str) -> ProviderAdapter:
    registry = build_registry()
    if provider_id not in registry:
        available = ", ".join(sorted(registry))
        raise KeyError(f"unknown provider '{provider_id}'; available: {available}")
    return registry[provider_id]


def build_registry() -> dict[str, ProviderAdapter]:
    catalog = load_catalog()
    adapters: dict[str, ProviderAdapter] = {}

    for provider_id, adapter_cls in _BUILTIN_ADAPTERS.items():
        if provider_id in catalog:
            adapters[provider_id] = adapter_cls(catalog[provider_id])

    for entry_point in _provider_entry_points():
        adapter = entry_point.load()
        if hasattr(adapter, "id"):
            adapters[adapter.id] = adapter
        elif callable(adapter):
            instance = adapter()
            adapters[instance.id] = instance

    return adapters


def is_provider_installed(provider_id: str) -> bool:
    catalog = load_catalog()
    if provider_id not in catalog:
        return False
    executable = catalog[provider_id].executable
    return shutil.which(executable) is not None


def _provider_entry_points() -> list[Any]:
    try:
        eps = entry_points(group="kcia.providers")
    except TypeError:
        eps = entry_points().get("kcia.providers", [])
    return list(eps)
