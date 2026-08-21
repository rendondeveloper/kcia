"""Environment diagnostics: can this machine actually run a pipeline?"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import typer

from kcia.config import model_in_catalog, resolve_agents
from kcia.mcp.config import resolve_enabled
from kcia.paths import control_plane_root, find_repo_root
from kcia.providers.base import AuthStatus
from kcia.providers.catalog import load_catalog
from kcia.providers.registry import AGENT_ROLES, build_registry

OK = "ok"
WARN = "warn"
FAIL = "fail"

_MARK = {OK: "✓", WARN: "!", FAIL: "✗"}


class _Report:
    def __init__(self) -> None:
        self.failures = 0
        self.warnings = 0

    def line(self, status: str, text: str, hint: str | None = None) -> None:
        typer.echo(f"  {_MARK[status]} {text}")
        if hint:
            typer.echo(f"      {hint}")
        if status == FAIL:
            self.failures += 1
        elif status == WARN:
            self.warnings += 1


def _report_git_flow(report: "_Report", repo: Path) -> None:
    """Whether `kcia branch start` knows where to branch from, and if a remote exists."""
    from kcia.git.flow import detect_base_branch, load_flow
    from kcia.git.repo import GitError, current_branch, is_git_repo, remotes

    if not is_git_repo(repo):
        report.line(WARN, "not a git worktree", "`kcia branch` and `kcia done` need one.")
        return
    try:
        flow = load_flow(repo)
        report.line(
            OK if flow.configured else WARN,
            f"git flow: {flow.describe()}" if flow.configured else "git flow: not configured",
            None if flow.configured else "Run `kcia init` to decide the branching model.",
        )
        detected = detect_base_branch(repo)
        report.line(OK, f"branch {current_branch(repo)}")
        report.line(
            OK if detected.certain else WARN,
            f"base branch {detected.name} ({detected.reason})",
            None if detected.certain else "`kcia branch start` will ask, or pass `--base`.",
        )
        names = remotes(repo)
        report.line(
            OK if names else WARN,
            f"remote {', '.join(names) if names else 'none'}",
            None if names else "`kcia done` needs one to push: `git remote add origin <url>`.",
        )
    except GitError as exc:
        report.line(WARN, f"git state unreadable: {exc}")


def _report_session_history(report: "_Report", repo: Path) -> None:
    """Whether `kcia session log`/`search` has anything to work with, and how."""
    history_file = repo / ".ai" / "history" / "sessions.jsonl"
    if not history_file.is_file():
        report.line(WARN, "session history: none logged yet", "Run `kcia session log --title ...`.")
        return
    from kcia.history.index import entry_count, uses_fts5

    count = entry_count(repo)
    mode = "full-text search" if uses_fts5(repo) else "substring search (no FTS5 in this Python)"
    report.line(OK, f"session history: {count} entr{'y' if count == 1 else 'ies'} ({mode})")


def doctor() -> None:
    """Check the toolchain, providers, agents and repository state."""
    report = _Report()

    typer.echo("Environment")
    version = f"{sys.version_info.major}.{sys.version_info.minor}"
    report.line(
        OK if sys.version_info >= (3, 11) else FAIL,
        f"Python {version}",
        None if sys.version_info >= (3, 11) else "kcia needs Python 3.11 or newer.",
    )
    git = shutil.which("git")
    report.line(OK if git else FAIL, f"git {git or 'not found'}")
    gh = shutil.which("gh")
    report.line(
        OK if gh else WARN,
        f"gh {gh or 'not found'}",
        None if gh else "Optional: git-flow `on_done: pr` needs it (https://cli.github.com).",
    )

    try:
        root = control_plane_root()
        resolved = root.is_dir()
    except Exception:  # noqa: BLE001 - any resolution failure is the same symptom
        root, resolved = None, False
    report.line(
        OK if resolved else FAIL,
        f"control plane {root if resolved else 'not found'}",
        None if resolved else "Reinstall with scripts/install.sh — kcia needs its git clone.",
    )

    typer.echo("")
    typer.echo("Providers")
    catalog = load_catalog()
    registry = build_registry()
    auth_by_provider: dict[str, AuthStatus] = {}
    for provider_id, entry in sorted(catalog.items()):
        adapter = registry.get(provider_id)
        if adapter is None or adapter.locate() is None:
            auth_by_provider[provider_id] = AuthStatus.NOT_INSTALLED
            report.line(WARN, f"{provider_id}: not installed", entry.install_hint)
            continue

        status = adapter.check_auth()
        auth_by_provider[provider_id] = status
        account = getattr(adapter, "account", lambda: None)()
        if status is AuthStatus.AUTHENTICATED:
            report.line(OK, f"{provider_id}: authenticated{f' as {account}' if account else ''}")
        elif status is AuthStatus.NOT_AUTHENTICATED:
            report.line(WARN, f"{provider_id}: not authenticated", entry.auth_hint)
        else:
            report.line(
                WARN,
                f"{provider_id}: installed, auth status unknown",
                "kcia could not read the provider's auth status; it may still work.",
            )

    typer.echo("")
    typer.echo("Agents")
    repo = find_repo_root()
    agents = resolve_agents(repo)
    for role in AGENT_ROLES:
        item = agents[role]
        label = f"{role}: {item.provider}/{item.model} ({item.origin})"

        if item.origin == "default":
            report.line(WARN, label, f"Not configured. Run `kcia agent set {role} <provider>`.")
        elif not model_in_catalog(item.provider, item.model):
            report.line(
                FAIL,
                label,
                f"`{item.model}` is not offered by `{item.provider}`. "
                f"Run `kcia agent models {item.provider}`.",
            )
        elif auth_by_provider.get(item.provider) is AuthStatus.NOT_INSTALLED:
            report.line(
                FAIL,
                label,
                f"`{item.provider}` is not installed, so this role cannot run.",
            )
        elif auth_by_provider.get(item.provider) is AuthStatus.NOT_AUTHENTICATED:
            report.line(FAIL, label, f"`{item.provider}` is not authenticated.")
        else:
            report.line(OK, label)

    typer.echo("")
    typer.echo("Repository")
    if repo is None:
        report.line(WARN, "not inside a git repository", "kcia works per repository.")
    else:
        report.line(OK, f"repo {repo}")
        manifest = repo / ".ai" / "manifest.yaml"
        report.line(
            OK if manifest.is_file() else WARN,
            f"manifest {'found' if manifest.is_file() else 'missing'}",
            None if manifest.is_file() else "Run `kcia init` in this repository.",
        )
        _report_git_flow(report, repo)
        _report_session_history(report, repo)

    if repo is not None:
        entries = resolve_enabled(repo)
        if entries:
            typer.echo("")
            typer.echo("MCP servers")
            for entry in entries:
                roles = ", ".join(entry.server.roles) if entry.server.roles else "all roles"
                report.line(OK, f"{entry.server.id}: enabled for {roles}")
            # kcia never holds these credentials, so it cannot verify the session.
            report.line(
                WARN,
                "authentication is owned by the provider CLI",
                "Check with `claude mcp list` or `cursor-agent mcp list`.",
            )

    typer.echo("")
    if report.failures:
        typer.echo(f"{report.failures} problem(s) block a run; {report.warnings} warning(s).")
        raise typer.Exit(code=1)
    if report.warnings:
        typer.echo(f"No blockers. {report.warnings} warning(s).")
        return
    typer.echo("Everything checks out.")
