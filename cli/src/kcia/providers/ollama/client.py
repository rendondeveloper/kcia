"""HTTP client for the Ollama daemon."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from typing import Any

import httpx

from kcia.providers.runner import DEFAULT_IDLE_TIMEOUT_SECONDS

DEFAULT_HOST = "http://127.0.0.1:11434"


class OllamaError(Exception):
    """Ollama API or transport failure."""


class OllamaClient:
    def __init__(
        self,
        base_url: str | None = None,
        *,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = (base_url or os.environ.get("OLLAMA_HOST") or DEFAULT_HOST).rstrip(
            "/"
        )
        self._owns_client = client is None
        self._client = client or httpx.Client(
            base_url=self.base_url,
            timeout=httpx.Timeout(
                connect=10.0,
                read=float(DEFAULT_IDLE_TIMEOUT_SECONDS),
                write=30.0,
                pool=10.0,
            ),
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def reachable(self) -> bool:
        try:
            self.version()
            return True
        except OllamaError:
            return False

    def version(self) -> dict[str, Any]:
        return self._get_json("/api/version")

    def tags(self) -> list[dict[str, Any]]:
        payload = self._get_json("/api/tags")
        models = payload.get("models")
        if isinstance(models, list):
            return [item for item in models if isinstance(item, dict)]
        return []

    def model_names(self) -> list[str]:
        names: list[str] = []
        for item in self.tags():
            name = item.get("name")
            if isinstance(name, str) and name and name not in names:
                names.append(name)
        return names

    def chat_stream(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        options: dict[str, Any] | None = None,
        think: bool | None = None,
    ) -> Iterator[dict[str, Any]]:
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": True,
        }
        if tools:
            body["tools"] = tools
        if options:
            body["options"] = options
        if think is not None:
            body["think"] = think
        try:
            with self._client.stream("POST", "/api/chat", json=body) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line:
                        continue
                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(payload, dict):
                        yield payload
        except httpx.HTTPError as exc:
            raise OllamaError(str(exc)) from exc

    def _get_json(self, path: str) -> dict[str, Any]:
        try:
            response = self._client.get(path)
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPError as exc:
            raise OllamaError(str(exc)) from exc
        if not isinstance(payload, dict):
            raise OllamaError(f"unexpected response from {path}")
        return payload
