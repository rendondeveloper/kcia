"""Bounded session-history snippets for prompts."""

from __future__ import annotations

import json
import re
from pathlib import Path

from kcia.history import index
from kcia.history import log as history_log

SUMMARY_LINE_MAX = 120


def history_query_from_text(text: str, *, max_words: int = 8) -> str:
    words = re.findall(r"[A-Za-z0-9]+", text)
    return " ".join(words[:max_words])


def search_related_history(
    repo_root: Path,
    query: str,
    *,
    limit: int = 3,
) -> tuple[str, set[str]]:
    """Return a markdown block and the session ids included."""
    if not history_log.log_path(repo_root).is_file() or not query.strip():
        return "", set()
    try:
        terms = query.split()
        seen: set[str] = set()
        hits = []
        for term in terms:
            for hit in index.search(repo_root, term, limit=limit):
                if hit.id in seen:
                    continue
                seen.add(hit.id)
                hits.append(hit)
                if len(hits) >= limit:
                    break
            if len(hits) >= limit:
                break
    except Exception:
        return "", set()
    if not hits:
        return "", set()
    return _format_related_block(hits), seen


def format_recent_history(
    repo_root: Path,
    *,
    limit: int = 3,
    exclude_ids: set[str] | None = None,
) -> str:
    exclude = exclude_ids or set()
    try:
        hits = index.list_recent(repo_root, limit=limit + len(exclude))
    except Exception:
        return ""
    lines: list[str] = []
    for hit in hits:
        if hit.id in exclude:
            continue
        lines.append(f"- {hit.timestamp} — {hit.title}")
        if len(lines) >= limit:
            break
    if not lines:
        return ""
    parts = ["## Recent history\n", *lines, ""]
    return "\n".join(parts)


def related_history_for_task(repo_root: Path, task_text: str, *, limit: int = 3) -> str:
    block, _ = search_related_history(
        repo_root,
        history_query_from_text(task_text),
        limit=limit,
    )
    return block


def _format_related_block(hits: list[index.SearchHit]) -> str:
    parts = ["## Related history\n"]
    for hit in hits:
        data = json.loads(hit.raw_json)
        line = f"- {hit.timestamp} — {hit.title}"
        summary = (data.get("summary") or "").strip().splitlines()
        if summary:
            line += f" — {summary[0][:SUMMARY_LINE_MAX]}"
        parts.append(line)
    parts.append("")
    return "\n".join(parts)
