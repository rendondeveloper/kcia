"""Local, regenerable SQLite index over `.ai/history/sessions.jsonl`.

Always rebuildable from the JSONL log, so this file lives under `.ai/local/`
and is safe to gitignore, delete, or regenerate on any machine.

FTS5 is used when the target Python's stdlib `sqlite3` was compiled with it;
otherwise search degrades to a `LIKE` scan with the same call signature, so
callers never need to know which path ran.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from kcia.history.log import SessionEntry, read_entries

INDEX_PATH = Path(".ai") / "local" / "history.sqlite3"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    rowid       INTEGER PRIMARY KEY,
    id          TEXT UNIQUE NOT NULL,
    timestamp   TEXT NOT NULL,
    title       TEXT NOT NULL,
    commit_sha  TEXT,
    branch      TEXT,
    task_id     TEXT,
    files_json  TEXT NOT NULL,
    raw_json    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

# Not created until an embeddings feature ships. Documented here so
# `sessions.rowid` is treated as a stable key going forward: this table would
# reference it directly, with no migration of `sessions`/`sessions_fts` needed.
#
# CREATE TABLE sessions_vec (
#     rowid     INTEGER PRIMARY KEY REFERENCES sessions(rowid),
#     model     TEXT NOT NULL,
#     dim       INTEGER NOT NULL,
#     embedding BLOB NOT NULL
# );


@dataclass(frozen=True)
class SearchHit:
    id: str
    timestamp: str
    title: str
    raw_json: str


def index_path(repo_root: Path) -> Path:
    return repo_root / INDEX_PATH


def fts5_supported(conn: sqlite3.Connection) -> bool:
    try:
        conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS _fts5_probe USING fts5(x)")
        conn.execute("DROP TABLE _fts5_probe")
        return True
    except sqlite3.OperationalError:
        return False


def _connect(repo_root: Path) -> sqlite3.Connection:
    path = index_path(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(_SCHEMA)
    if _meta_get(conn, "fts5") is None:
        supported = fts5_supported(conn)
        if supported:
            conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS sessions_fts USING fts5("
                "title, summary, decisions, files_text, "
                "content='sessions', content_rowid='rowid')"
            )
        _meta_set(conn, "fts5", "1" if supported else "0")
        conn.commit()
    return conn


def _meta_get(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def _meta_set(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta(key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def _has_fts(conn: sqlite3.Connection) -> bool:
    return _meta_get(conn, "fts5") == "1"


def _insert(conn: sqlite3.Connection, entry: SessionEntry) -> None:
    files_json = json.dumps(entry.files, ensure_ascii=False)
    files_text = " ".join(item.get("path", "") for item in entry.files)
    raw_json = json.dumps(entry.to_json(), ensure_ascii=False)
    cur = conn.execute(
        "INSERT OR IGNORE INTO sessions "
        "(id, timestamp, title, commit_sha, branch, task_id, files_json, raw_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            entry.id,
            entry.timestamp,
            entry.title,
            entry.commit_sha,
            entry.branch,
            entry.task_id,
            files_json,
            raw_json,
        ),
    )
    if cur.rowcount and _has_fts(conn):
        conn.execute(
            "INSERT INTO sessions_fts(rowid, title, summary, decisions, files_text) "
            "VALUES (?, ?, ?, ?, ?)",
            (cur.lastrowid, entry.title, entry.summary, " ".join(entry.decisions), files_text),
        )


def sync(repo_root: Path, entry: SessionEntry) -> None:
    """Add one newly-logged entry to the index without a full rebuild."""
    conn = _connect(repo_root)
    try:
        _insert(conn, entry)
        conn.commit()
    finally:
        conn.close()


def reindex(repo_root: Path) -> int:
    """Rebuild the index from scratch off the JSONL log. Returns the entry count."""
    path = index_path(repo_root)
    if path.exists():
        path.unlink()
    conn = _connect(repo_root)
    try:
        count = 0
        for entry in read_entries(repo_root):
            _insert(conn, entry)
            count += 1
        conn.commit()
        return count
    finally:
        conn.close()


def ensure(repo_root: Path) -> None:
    """Make sure the index exists and has at least as many rows as the log."""
    log_count = len(read_entries(repo_root))
    if log_count == 0:
        return
    conn = _connect(repo_root)
    try:
        (indexed,) = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()
    finally:
        conn.close()
    if indexed < log_count:
        reindex(repo_root)


def entry_count(repo_root: Path) -> int:
    if not index_path(repo_root).is_file():
        return len(read_entries(repo_root))
    conn = _connect(repo_root)
    try:
        (count,) = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()
        return count
    finally:
        conn.close()


def uses_fts5(repo_root: Path) -> bool:
    conn = _connect(repo_root)
    try:
        return _has_fts(conn)
    finally:
        conn.close()


def search(repo_root: Path, query: str, *, limit: int = 10) -> list[SearchHit]:
    ensure(repo_root)
    conn = _connect(repo_root)
    try:
        if _has_fts(conn):
            rows = conn.execute(
                "SELECT sessions.id, sessions.timestamp, sessions.title, sessions.raw_json "
                "FROM sessions_fts "
                "JOIN sessions ON sessions.rowid = sessions_fts.rowid "
                "WHERE sessions_fts MATCH ? "
                "ORDER BY rank LIMIT ?",
                (query, limit),
            ).fetchall()
        else:
            terms = [term for term in query.split() if term]
            clauses = " AND ".join(
                "(title LIKE ? OR files_json LIKE ? OR raw_json LIKE ?)" for _ in terms
            ) or "1"
            params: list[str] = []
            for term in terms:
                like = f"%{term}%"
                params.extend([like, like, like])
            rows = conn.execute(
                f"SELECT id, timestamp, title, raw_json FROM sessions "
                f"WHERE {clauses} ORDER BY timestamp DESC LIMIT ?",
                (*params, limit),
            ).fetchall()
        return [SearchHit(id=r[0], timestamp=r[1], title=r[2], raw_json=r[3]) for r in rows]
    finally:
        conn.close()


def list_recent(repo_root: Path, *, limit: int = 20, since: str | None = None) -> list[SearchHit]:
    ensure(repo_root)
    conn = _connect(repo_root)
    try:
        if since:
            rows = conn.execute(
                "SELECT id, timestamp, title, raw_json FROM sessions "
                "WHERE timestamp >= ? ORDER BY timestamp DESC LIMIT ?",
                (since, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, timestamp, title, raw_json FROM sessions "
                "ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [SearchHit(id=r[0], timestamp=r[1], title=r[2], raw_json=r[3]) for r in rows]
    finally:
        conn.close()


def get(repo_root: Path, entry_id: str) -> SearchHit | None:
    ensure(repo_root)
    conn = _connect(repo_root)
    try:
        row = conn.execute(
            "SELECT id, timestamp, title, raw_json FROM sessions WHERE id = ?",
            (entry_id,),
        ).fetchone()
        return SearchHit(id=row[0], timestamp=row[1], title=row[2], raw_json=row[3]) if row else None
    finally:
        conn.close()
