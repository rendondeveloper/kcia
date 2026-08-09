"""Provider catalog models and loader."""

from __future__ import annotations

from dataclasses import dataclass

import yaml

from kcia.paths import control_plane_root


@dataclass(frozen=True)
class ProviderModel:
    id: str
    tier: str | None = None
    best_for: list[str] | None = None


@dataclass(frozen=True)
class ProviderCatalogEntry:
    id: str
    display_name: str
    executable: str
    install_hint: str
    auth_hint: str
    models: list[ProviderModel]
    default_model: str


def load_catalog() -> dict[str, ProviderCatalogEntry]:
    catalog_path = control_plane_root() / "providers" / "catalog.yaml"
    data = yaml.safe_load(catalog_path.read_text(encoding="utf-8")) or {}
    entries: dict[str, ProviderCatalogEntry] = {}
    for provider_id, raw in (data.get("providers") or {}).items():
        models = [
            ProviderModel(
                id=item["id"],
                tier=item.get("tier"),
                best_for=item.get("best_for"),
            )
            for item in raw.get("models", [])
        ]
        entries[provider_id] = ProviderCatalogEntry(
            id=provider_id,
            display_name=raw.get("display_name", provider_id),
            executable=raw.get("executable", provider_id),
            install_hint=raw.get("install_hint", ""),
            auth_hint=raw.get("auth_hint", ""),
            models=models,
            default_model=raw.get("default_model", models[0].id if models else ""),
        )
    return entries
