"""Provider adapters."""

from kcia.providers.base import (
    AuthStatus,
    ProviderAdapter,
    ProviderCapabilities,
    RunRequest,
    RunResult,
)
from kcia.providers.catalog import ProviderCatalogEntry, load_catalog
from kcia.providers.claude import ClaudeAdapter
from kcia.providers.cursor import CursorAdapter
from kcia.providers.registry import AGENT_ROLES, build_registry, get_adapter, is_provider_installed

__all__ = [
    "AGENT_ROLES",
    "AuthStatus",
    "ClaudeAdapter",
    "CursorAdapter",
    "ProviderAdapter",
    "ProviderCapabilities",
    "ProviderCatalogEntry",
    "RunRequest",
    "RunResult",
    "build_registry",
    "get_adapter",
    "is_provider_installed",
    "load_catalog",
]
