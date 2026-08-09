"""Provider availability and authentication checks."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from kcia.providers.base import AuthStatus
from kcia.providers.catalog import load_catalog
from kcia.providers.claude import ClaudeAdapter
from kcia.providers.cursor import CursorAdapter
from kcia.waves.runner import check_agents_ready

ROOT = Path(__file__).resolve().parents[1]
KCIA = ROOT / ".venv" / "bin" / "kcia"


def _completed(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


@pytest.fixture()
def claude() -> ClaudeAdapter:
    return ClaudeAdapter(load_catalog()["claude"])


@pytest.fixture()
def cursor() -> CursorAdapter:
    return CursorAdapter(load_catalog()["cursor"])


def test_claude_reports_not_installed_when_binary_missing(claude: ClaudeAdapter) -> None:
    with patch.object(ClaudeAdapter, "locate", return_value=None):
        assert claude.check_auth() is AuthStatus.NOT_INSTALLED


def test_claude_detects_logged_out(claude: ClaudeAdapter) -> None:
    """A bare `--version` succeeds when logged out, so the JSON must be read."""
    payload = json.dumps({"loggedIn": False})
    with (
        patch.object(ClaudeAdapter, "locate", return_value="/usr/bin/claude"),
        patch("kcia.providers.claude.subprocess.run", return_value=_completed(payload)),
    ):
        assert claude.check_auth() is AuthStatus.NOT_AUTHENTICATED


def test_claude_detects_logged_in(claude: ClaudeAdapter) -> None:
    payload = json.dumps({"loggedIn": True, "email": "a@b.c", "subscriptionType": "pro"})
    with (
        patch.object(ClaudeAdapter, "locate", return_value="/usr/bin/claude"),
        patch("kcia.providers.claude.subprocess.run", return_value=_completed(payload)),
    ):
        assert claude.check_auth() is AuthStatus.AUTHENTICATED
        assert claude.account() == "a@b.c (pro)"


def test_claude_unparseable_status_is_unknown_not_authenticated(claude: ClaudeAdapter) -> None:
    with (
        patch.object(ClaudeAdapter, "locate", return_value="/usr/bin/claude"),
        patch("kcia.providers.claude.subprocess.run", return_value=_completed("not json")),
    ):
        assert claude.check_auth() is AuthStatus.UNKNOWN


def test_cursor_detects_logged_in_and_out(cursor: CursorAdapter) -> None:
    with patch.object(CursorAdapter, "locate", return_value="/usr/bin/cursor-agent"):
        with patch(
            "kcia.providers.cursor.subprocess.run",
            return_value=_completed("✓ Logged in as a@b.c"),
        ):
            assert cursor.check_auth() is AuthStatus.AUTHENTICATED
            assert cursor.account() == "a@b.c"
        with patch(
            "kcia.providers.cursor.subprocess.run",
            return_value=_completed("Not logged in", returncode=1),
        ):
            assert cursor.check_auth() is AuthStatus.NOT_AUTHENTICATED


def test_timeout_is_unknown_rather_than_a_crash(claude: ClaudeAdapter) -> None:
    with (
        patch.object(ClaudeAdapter, "locate", return_value="/usr/bin/claude"),
        patch(
            "kcia.providers.claude.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="claude", timeout=30),
        ),
    ):
        assert claude.check_auth() is AuthStatus.UNKNOWN


def test_preflight_reports_missing_provider_before_any_wave_runs() -> None:
    with patch("kcia.waves.runner.get_adapter") as get_adapter:
        get_adapter.return_value.locate.return_value = None
        problems = check_agents_ready(None)

    assert len(problems) == 2, problems
    assert any(p.startswith("planner needs") for p in problems)
    assert any(p.startswith("builder needs") for p in problems)
    # The install hint must reach the user, not just the failure.
    assert any("npm i -g" in p or "Instala Cursor" in p for p in problems)


def test_preflight_reports_logged_out_provider() -> None:
    with patch("kcia.waves.runner.get_adapter") as get_adapter:
        adapter = get_adapter.return_value
        adapter.locate.return_value = "/usr/bin/claude"
        adapter.check_auth.return_value = AuthStatus.NOT_AUTHENTICATED
        problems = check_agents_ready(None)

    assert all("not authenticated" in p for p in problems)


def test_preflight_is_silent_when_everything_is_ready() -> None:
    with patch("kcia.waves.runner.get_adapter") as get_adapter:
        adapter = get_adapter.return_value
        adapter.locate.return_value = "/usr/bin/claude"
        adapter.check_auth.return_value = AuthStatus.AUTHENTICATED
        assert check_agents_ready(None) == []


def test_doctor_runs_and_reports_sections() -> None:
    result = subprocess.run([str(KCIA), "doctor"], capture_output=True, text=True, cwd=ROOT)
    assert result.returncode in (0, 1)
    for section in ("Environment", "Providers", "Agents", "Repository"):
        assert section in result.stdout
    assert "NOT IMPLEMENTED" not in result.stdout
