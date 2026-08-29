"""Compose bounded prompts for `kcia ask`."""

from __future__ import annotations

from pathlib import Path

from kcia.ask.conversation import AskConversation, AskTurn
from kcia.history.prompt_context import (
    format_recent_history,
    history_query_from_text,
    search_related_history,
)
from kcia.paths import control_plane_root
from kcia.profiles.loader import load_registry
from kcia.profiles.predicates import MAX_FILE_BYTES
from kcia.render import render_template
from kcia.waves.budget import estimate_tokens
from kcia.waves.repomap import build_repo_map
from kcia.waves.session import Session, context_dir, load_manifest

ASK_MAX_PROMPT_TOKENS = 8_000
MAX_PRIOR_TURNS = 8
MAX_TURN_CHARS = 2_000
RELATED_HISTORY_LIMIT = 5
RECENT_HISTORY_LIMIT = 3

_DROP_ORDER = ("prior-turns", "recent-history", "related-history", "repo-map")


def build_ask_prompt(
    repo_root: Path,
    question: str,
    conversation: AskConversation,
    *,
    resume: bool,
) -> str:
    sections: dict[str, str] = {}

    if resume:
        sections["role"] = _resume_role()
        sections["instruction"] = _instruction()
        related, seen = search_related_history(
            repo_root,
            history_query_from_text(question),
            limit=RELATED_HISTORY_LIMIT,
        )
        sections["related-history"] = related
        sections["recent-history"] = format_recent_history(
            repo_root,
            limit=RECENT_HISTORY_LIMIT,
            exclude_ids=seen,
        )
        sections["question"] = _question_section(question)
    else:
        sections["role"] = _full_role()
        sections["guardrails"] = _guardrails()
        sections["instruction"] = _instruction()
        sections["project-context"] = _read_context_file(repo_root, "project.md")
        sections["repo-map"] = _repo_map(repo_root)
        related, seen = search_related_history(
            repo_root,
            history_query_from_text(question),
            limit=RELATED_HISTORY_LIMIT,
        )
        sections["related-history"] = related
        sections["recent-history"] = format_recent_history(
            repo_root,
            limit=RECENT_HISTORY_LIMIT,
            exclude_ids=seen,
        )
        sections["active-task"] = _active_task_context(repo_root)
        sections["prior-turns"] = _prior_turns(conversation.turns)
        sections["attached-path"] = _optional_path_attachment(repo_root, question)
        sections["question"] = _question_section(question)

    return _apply_budget(sections)


def _apply_budget(sections: dict[str, str]) -> str:
    kept = {name: content for name, content in sections.items() if content}
    while _total_tokens(kept) > ASK_MAX_PROMPT_TOKENS:
        dropped = False
        for name in _DROP_ORDER:
            if name in kept:
                kept.pop(name)
                dropped = True
                break
        if not dropped:
            break
    return "\n".join(part for part in kept.values() if part)


def _total_tokens(sections: dict[str, str]) -> int:
    return sum(estimate_tokens(content) for content in sections.values())


def _full_role() -> str:
    return (
        "# Role: planner (read-only Q&A)\n\n"
        "You are the kcia planner answering a direct question about this repository. "
        "Do not edit files, run git writes, or start implementation work.\n"
    )


def _resume_role() -> str:
    return (
        "# Follow-up (read-only Q&A)\n\n"
        "Continue the same kcia ask conversation. Do not edit files or run git writes.\n"
    )


def _guardrails() -> str:
    guardrails_dir = control_plane_root() / "guardrails"
    chunks: list[str] = ["## Guardrails\n"]
    for name in ("input-filter.md", "tool-control.md"):
        path = guardrails_dir / name
        if path.is_file():
            chunks.append(path.read_text(encoding="utf-8"))
            chunks.append("")
    return "\n".join(chunks)


def _instruction() -> str:
    return render_template(control_plane_root() / "prompts", "ask.md.j2")


def _question_section(question: str) -> str:
    return f"## Question\n\n{question.strip()}\n"


def _read_context_file(repo_root: Path, name: str) -> str:
    path = context_dir(repo_root) / name
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def _repo_map(repo_root: Path) -> str:
    manifest = load_manifest(repo_root)
    if manifest is None:
        return ""
    registry = load_registry(repo_root)
    return build_repo_map(manifest, registry, repo_root)


def _active_task_context(repo_root: Path) -> str:
    try:
        session = Session.load(repo_root)
    except FileNotFoundError:
        return ""

    from kcia.waves.definitions import load_waves

    task = session.task
    wave_bits = [
        f"{wave.id}: {session.wave_status(wave.id)}"
        for wave in load_waves()
        if session.wave_status(wave.id) != "pending"
    ]
    parts = [
        "## Active task\n",
        f"- id: {task.get('id')}\n",
        f"- title: {task.get('title')}\n",
    ]
    if wave_bits:
        parts.append(f"- waves: {', '.join(wave_bits)}\n")
    return "".join(parts)


def _prior_turns(turns: list[AskTurn]) -> str:
    if not turns:
        return ""
    recent = turns[-MAX_PRIOR_TURNS:]
    parts = ["## Prior turns\n"]
    for turn in recent:
        label = "User" if turn.role == "user" else "Planner"
        text = turn.text.strip()
        if len(text) > MAX_TURN_CHARS:
            text = text[: MAX_TURN_CHARS - 3] + "..."
        parts.extend([f"### {label}", text, ""])
    return "\n".join(parts)


def _optional_path_attachment(repo_root: Path, question: str) -> str:
    for token in question.replace(",", " ").split():
        candidate = token.strip("'\"`")
        if not candidate or candidate.startswith("http"):
            continue
        path = (repo_root / candidate).resolve()
        try:
            path.relative_to(repo_root.resolve())
        except ValueError:
            continue
        if not path.is_file():
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size > MAX_FILE_BYTES:
            continue
        rel = path.relative_to(repo_root.resolve())
        text = path.read_text(encoding="utf-8", errors="replace")
        if len(text) > MAX_FILE_BYTES:
            text = text[: MAX_FILE_BYTES - 3] + "..."
        return f"## Attached file: {rel}\n\n```\n{text}\n```\n"
    return ""
