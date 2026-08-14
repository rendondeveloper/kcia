from pathlib import Path

from kcia.history import index, log


def _entry(title: str = "Add caching layer") -> log.SessionEntry:
    return log.SessionEntry(
        id=log.new_id(),
        timestamp="2026-08-14T00:00:00Z",
        title=title,
        summary="Added an in-memory cache in front of the repository layer.",
        decisions=["Use an LRU cache, no external dependency"],
        files=[{"path": "lib/cache.dart", "change": "created"}],
        commit_sha=None,
        branch="main",
        task_id=None,
    )


def test_sync_and_search_with_fts5(tmp_path: Path) -> None:
    entry = _entry()
    index.sync(tmp_path, entry)

    hits = index.search(tmp_path, "caching")
    assert any(hit.id == entry.id for hit in hits)


def test_search_falls_back_to_like_when_fts5_unavailable(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(index, "fts5_supported", lambda conn: False)
    entry = _entry()
    index.sync(tmp_path, entry)

    assert index.uses_fts5(tmp_path) is False
    hits = index.search(tmp_path, "cache.dart")
    assert any(hit.id == entry.id for hit in hits)


def test_reindex_rebuilds_from_jsonl(tmp_path: Path) -> None:
    entry = _entry()
    log.append_entry(tmp_path, entry)

    count = index.reindex(tmp_path)
    assert count == 1
    assert index.entry_count(tmp_path) == 1


def test_get_returns_none_for_unknown_id(tmp_path: Path) -> None:
    entry = _entry()
    index.sync(tmp_path, entry)
    assert index.get(tmp_path, "does-not-exist") is None
    assert index.get(tmp_path, entry.id) is not None
