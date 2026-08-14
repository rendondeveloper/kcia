import subprocess
from pathlib import Path

from typer.testing import CliRunner

from kcia.main import app

runner = CliRunner()


def _git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    return repo


def test_session_log_appends_jsonl_and_search_finds_it(tmp_path: Path, monkeypatch) -> None:
    repo = _git_repo(tmp_path)
    monkeypatch.chdir(repo)

    result = runner.invoke(
        app,
        [
            "session",
            "log",
            "--title",
            "Add session command group",
            "--summary",
            "Introduced kcia session log/search.",
            "--decision",
            "Use SQLite FTS5 with a LIKE fallback",
            "--file",
            "cli/src/kcia/commands/session.py:created",
        ],
    )
    assert result.exit_code == 0, result.output

    log_path = repo / ".ai" / "history" / "sessions.jsonl"
    assert log_path.is_file()
    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1

    search = runner.invoke(app, ["session", "search", "session"])
    assert search.exit_code == 0, search.output
    assert "Add session command group" in search.output

    listed = runner.invoke(app, ["session", "list"])
    assert listed.exit_code == 0
    assert "Add session command group" in listed.output


def test_session_reindex_rebuilds_from_log(tmp_path: Path, monkeypatch) -> None:
    repo = _git_repo(tmp_path)
    monkeypatch.chdir(repo)
    runner.invoke(app, ["session", "log", "--title", "First"])

    index_path = repo / ".ai" / "local" / "history.sqlite3"
    assert index_path.is_file()
    index_path.unlink()

    result = runner.invoke(app, ["session", "reindex"])
    assert result.exit_code == 0, result.output
    assert "Reindexed 1 session" in result.output
    assert index_path.is_file()


def test_session_show_unknown_id_fails(tmp_path: Path, monkeypatch) -> None:
    repo = _git_repo(tmp_path)
    monkeypatch.chdir(repo)
    result = runner.invoke(app, ["session", "show", "does-not-exist"])
    assert result.exit_code == 1
