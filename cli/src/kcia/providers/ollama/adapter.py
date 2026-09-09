"""Ollama provider adapter."""

from __future__ import annotations

from collections.abc import Callable

from kcia.providers.base import AuthStatus, ProviderCapabilities, RunRequest, RunResult
from kcia.providers.catalog import ProviderCatalogEntry
from kcia.providers.events import StreamEvent, StreamState
from kcia.providers.ollama.client import OllamaClient
from kcia.providers.ollama.loop import DEFAULT_NUM_CTX, run_agent_loop

DEFAULT_NUM_CTX_FALLBACK = DEFAULT_NUM_CTX


class OllamaAdapter:
    id = "ollama"

    def __init__(self, catalog: ProviderCatalogEntry) -> None:
        self._catalog = catalog
        self.display_name = catalog.display_name
        self.executable = catalog.executable
        self.capabilities = ProviderCapabilities(
            supports_streaming=True,
            supports_sessions=False,
            supports_effort=True,
            supports_tool_restriction=True,
            supports_mcp_config=False,
        )
        self._client: OllamaClient | None = None

    def _get_client(self) -> OllamaClient:
        if self._client is None:
            self._client = OllamaClient()
        return self._client

    def locate(self) -> str | None:
        client = self._get_client()
        if client.reachable():
            return client.base_url
        return None

    def list_models(self) -> list[str]:
        live = self.discover_models()
        if live:
            return live
        return [model.id for model in self._catalog.models]

    def discover_models(self) -> list[str] | None:
        try:
            names = self._get_client().model_names()
        except Exception:
            return None
        return names or None

    def check_auth(self) -> AuthStatus:
        if self.locate() is None:
            return AuthStatus.NOT_INSTALLED
        return AuthStatus.AUTHENTICATED

    def account(self) -> str | None:
        client = self._get_client()
        if not client.reachable():
            return None
        try:
            version = client.version().get("version", "unknown")
        except Exception:
            version = "unknown"
        return f"{client.base_url} (ollama {version})"

    def num_ctx_for_model(self, model_id: str) -> int:
        for model in self._catalog.models:
            if model.id == model_id and model.num_ctx:
                return model.num_ctx
        return DEFAULT_NUM_CTX_FALLBACK

    def effective_context_tokens(self, model_id: str) -> int:
        return self.num_ctx_for_model(model_id)

    def run(
        self,
        req: RunRequest,
        *,
        on_event: Callable[[StreamEvent], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> RunResult:
        client = self._get_client()
        return run_agent_loop(
            client,
            req,
            num_ctx=self.num_ctx_for_model(req.model),
            on_event=on_event,
            should_cancel=should_cancel,
        )

    def build_command(self, req: RunRequest) -> list[str]:
        raise NotImplementedError(
            "Ollama uses in-process HTTP execution; call adapter.run() instead."
        )

    def parse_stream_line(self, line: str, state: StreamState) -> list[StreamEvent]:
        raise NotImplementedError(
            "Ollama uses in-process HTTP execution; call adapter.run() instead."
        )

    def new_session_id(self) -> str | None:
        return None
