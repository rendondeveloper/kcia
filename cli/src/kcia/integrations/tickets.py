"""Fetch a work item into `.ai/context/ticket.md`.

kcia never calls the Atlassian API itself. The fetch is one short invocation of
the planner's provider CLI with the Atlassian MCP config attached — the same
mechanism the waves use — so the credentials stay owned by that CLI and the
result lands on disk where every wave already reads it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from kcia.config import resolve_agents
from kcia.mcp.config import (
    CURSOR_CONFIG,
    allowed_tools_for_role,
    render_claude_config,
    servers_for_role,
)
from kcia.paths import control_plane_root
from kcia.providers.base import RunRequest
from kcia.providers.registry import get_adapter
from kcia.providers.events import StreamEvent
from kcia.providers.runner import call_provider, run_provider
from kcia.render import render_template
from kcia.waves.blocked import detect_blocked
from kcia.waves.session import context_dir, runs_dir

TICKET_FILE = "ticket.md"
ATLASSIAN_SERVER = "atlassian"
FETCH_ROLE = "planner"


@dataclass(frozen=True)
class FetchResult:
    path: Path | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.path is not None


def atlassian_available(repo_root: Path) -> bool:
    """Whether the role that fetches has the Atlassian server enabled."""
    return any(
        entry.server.id == ATLASSIAN_SERVER for entry in servers_for_role(repo_root, FETCH_ROLE)
    )


def fetch_agent(repo_root: Path):
    """The agent that performs the fetch, for progress labelling."""
    return resolve_agents(repo_root)[FETCH_ROLE]


def fetch_ticket(
    repo_root: Path,
    ticket_key: str,
    *,
    provider_runner=None,
    on_event: Callable[[StreamEvent], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> FetchResult:
    if not atlassian_available(repo_root):
        return FetchResult(
            error=(
                "the Atlassian MCP server is not enabled for the planner. "
                "Run `kcia mcp add atlassian`."
            )
        )

    agent = resolve_agents(repo_root)[FETCH_ROLE]
    adapter = get_adapter(agent.provider)
    if adapter.locate() is None:
        return FetchResult(error=f"`{agent.provider}` is not installed.")

    if agent.provider == "claude":
        mcp_config = render_claude_config(
            repo_root, FETCH_ROLE, runs_dir(repo_root) / f"mcp-{FETCH_ROLE}.json"
        )
    else:
        mcp_config = repo_root / CURSOR_CONFIG

    prompt = render_template(
        control_plane_root() / "templates", "ticket-fetch.md.j2", ticket_key=ticket_key
    )
    request = RunRequest(
        prompt=prompt,
        model=agent.model,
        allow_edits=False,
        stream=adapter.capabilities.supports_streaming,
        workspace_dirs=[repo_root],
        session_id=None,
        resume=False,
        effort=agent.effort,
        allowed_tools=None,
        disallowed_tools=None,
        cwd=repo_root,
        mcp_config=mcp_config,
        mcp_tools=allowed_tools_for_role(repo_root, FETCH_ROLE),
    )

    runner = provider_runner or run_provider
    try:
        result = call_provider(runner, adapter, request, on_event, should_cancel)
    except Exception as exc:  # noqa: BLE001 - surfaced to the user, never fatal
        return FetchResult(error=f"the provider call failed: {exc}")

    if getattr(result, "cancel_reason", None) == "cancelled by user":
        return FetchResult(error="cancelled.")

    text = (getattr(result, "output_text", "") or "").strip()
    if not text:
        return FetchResult(error="the provider returned nothing.")

    blocked = detect_blocked(text)
    if blocked:
        return FetchResult(error=blocked)

    path = context_dir(repo_root) / TICKET_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + "\n", encoding="utf-8")
    return FetchResult(path=path)
