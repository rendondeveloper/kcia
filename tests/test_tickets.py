"""Fetching a Jira issue into the task context."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from kcia.integrations.tickets import atlassian_available, fetch_ticket
from kcia.mcp.config import save_enabled
from kcia.providers.base import RunResult
from kcia.waves.session import Session, context_dir

ROOT = Path(__file__).resolve().parents[1]
KCIA = ROOT / ".venv" / "bin" / "kcia"

TICKET = """# PROJ-123 — Loader on launch

## Description
Show a progress indicator while the app starts.
"""


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    target = tmp_path / "melos_mono"
    shutil.copytree(ROOT / "tests" / "fixtures" / "repos" / "melos_mono", target)
    subprocess.run(["git", "init", "-q", str(target)], check=True)
    subprocess.run(
        [str(KCIA), "init", "--yes", "--path", str(target)], check=True, capture_output=True
    )
    return target


def _runner(text: str, seen: dict | None = None):
    def run(_adapter, req, **_kwargs):
        if seen is not None:
            seen["prompt"] = req.prompt
            seen["mcp_config"] = req.mcp_config
            seen["allow_edits"] = req.allow_edits
        return RunResult(output_text=text, exit_code=0)

    return run


def test_requires_the_atlassian_server(repo: Path) -> None:
    assert not atlassian_available(repo)
    result = fetch_ticket(repo, "PROJ-123", provider_runner=_runner(TICKET))
    assert not result.ok
    assert "kcia mcp add atlassian" in (result.error or "")


def test_writes_the_issue_into_the_task_context(repo: Path) -> None:
    save_enabled(repo, {"atlassian": {}})
    result = fetch_ticket(repo, "PROJ-123", provider_runner=_runner(TICKET))

    assert result.ok
    assert result.path == context_dir(repo) / "ticket.md"
    assert "Show a progress indicator" in result.path.read_text(encoding="utf-8")


def test_the_fetch_is_read_only_and_carries_the_mcp_config(repo: Path) -> None:
    """It must not be able to edit the repository, and it needs the server."""
    save_enabled(repo, {"atlassian": {}})
    seen: dict = {}
    fetch_ticket(repo, "PROJ-123", provider_runner=_runner(TICKET, seen))

    assert seen["allow_edits"] is False
    assert seen["mcp_config"] is not None
    assert "PROJ-123" in seen["prompt"]


def test_a_blocked_response_is_reported_not_written(repo: Path) -> None:
    """`BLOCKED: no access` must not become the ticket body."""
    save_enabled(repo, {"atlassian": {}})
    result = fetch_ticket(
        repo, "PROJ-123", provider_runner=_runner("BLOCKED: issue not found")
    )

    assert not result.ok
    assert result.error == "issue not found"
    assert not (context_dir(repo) / "ticket.md").exists()


def test_an_empty_response_is_reported_not_written(repo: Path) -> None:
    save_enabled(repo, {"atlassian": {}})
    result = fetch_ticket(repo, "PROJ-123", provider_runner=_runner("   "))
    assert not result.ok
    assert not (context_dir(repo) / "ticket.md").exists()


def test_a_provider_crash_is_reported_not_raised(repo: Path) -> None:
    """A failed fetch must not lose the task that was just created."""
    save_enabled(repo, {"atlassian": {}})

    def boom(*_args, **_kwargs):
        raise RuntimeError("network down")

    result = fetch_ticket(repo, "PROJ-123", provider_runner=boom)
    assert not result.ok
    assert "network down" in (result.error or "")


def test_the_fetched_body_reaches_the_wave_prompt(repo: Path) -> None:
    """The whole point: ticket mode used to send only the key."""
    from kcia.waves.definitions import get_wave
    from kcia.waves.prompts import build_prompt

    save_enabled(repo, {"atlassian": {}})
    Session.create(repo, text="PROJ-123", mode="ticket", ticket_key="PROJ-123")
    fetch_ticket(repo, "PROJ-123", provider_runner=_runner(TICKET))

    prompt = build_prompt(get_wave("understanding"), Session.load(repo))
    assert "Ticket: `PROJ-123`" in prompt
    assert "Show a progress indicator" in prompt


def test_prompt_mode_never_reads_a_stale_ticket(repo: Path) -> None:
    """ticket.md is only injected in ticket mode, so a leftover file is inert."""
    from kcia.waves.definitions import get_wave
    from kcia.waves.prompts import build_prompt

    context_dir(repo).mkdir(parents=True, exist_ok=True)
    (context_dir(repo) / "ticket.md").write_text(TICKET, encoding="utf-8")
    Session.create(repo, text="fix the overflow", mode="prompt")

    prompt = build_prompt(get_wave("understanding"), Session.load(repo))
    assert "Show a progress indicator" not in prompt
