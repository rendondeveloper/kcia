# Inject related session history into the `understanding` wave prompt

## Context

`kcia session log`/`search` (already shipped) is a filing cabinet nobody reads automatically: `kcia task init` and `kcia wave run` never query `.ai/history/`, so every task starts cold even when a closely related session was logged minutes ago. The goal is to close this with a plain, deterministic Python-side search — no extra LLM/tool round-trip, no meaningful token increase — that surfaces just enough of the "why" (title + one line of the decision/summary) for the model to recognize a relationship to past work, not a heavy context dump.

`cli/src/kcia/waves/prompts.py:build_prompt_with_stats` composes every wave prompt as an ordered list of named, token-counted sections (`role`, `guardrails`, `task-statement`, `project-context`, `repo-map`, `profile:*`, ...). Adding a new bounded section here, built by calling `kcia.history.index.search()` directly in Python before the prompt is assembled, is a natural fit — no new machinery needed, same `add_section()` pattern every other section uses.

## Design

**Where**: a new `related-history` section, inserted right after `task-statement` (before `project-context`) — logically it answers "what's the task" then "has this happened before" then "here are the current project facts."

**Which wave(s)**: only `understanding` (the wave that scopes the problem and is the one place with `can_ask_questions: true`). Controlled by a new `WaveDefinition.include_history: bool = False` field, set `true` only for `understanding` in `control-plane/waves/waves.yaml`, so no other wave pays the (tiny) query cost or token cost.

**Query**: sanitized task title/prompt (`session.task["prompt"] or session.task["title"]`), stripped to alphanumeric words only and capped at ~8 words, to avoid two failure modes: (a) FTS5's `MATCH` query syntax choking on punctuation/operators from free-form task text, (b) an overlong query. `index.search()` is wrapped in `try/except Exception` so a malformed query or any sqlite hiccup degrades to "no related history" instead of ever breaking a wave run — matching the graceful-degradation stance the FTS5 fallback already has.

**Bound**: top 3 hits, each rendered as one line (`- {timestamp} — {title}`) plus, when present, the first line of `summary` truncated to ~120 chars. No full JSON dump. Skip entirely (no sqlite touch at all) when `.ai/history/sessions.jsonl` doesn't exist yet, so untouched repos never get an empty `.ai/local/history.sqlite3` created just by running a wave.

**Content shape** (mirrors `_task_statement`'s style in the same file):
```python
def _related_history(session: Session, *, limit: int = 3) -> str:
    from kcia.history import index, log as history_log

    if not history_log.log_path(session.repo_root).is_file():
        return ""
    query = _history_query(session)
    if not query:
        return ""
    try:
        hits = index.search(session.repo_root, query, limit=limit)
    except Exception:
        return ""
    if not hits:
        return ""
    parts = ["## Related history\n"]
    for hit in hits:
        data = json.loads(hit.raw_json)
        line = f"- {hit.timestamp} — {hit.title}"
        summary = (data.get("summary") or "").strip().splitlines()
        if summary:
            line += f" — {summary[0][:120]}"
        parts.append(line)
    parts.append("")
    return "\n".join(parts)


def _history_query(session: Session) -> str:
    import re

    task = session.task
    text = (task.get("prompt") or task.get("title") or "")
    words = re.findall(r"[A-Za-z0-9]+", text)
    return " ".join(words[:8])
```
Called in `build_prompt_with_stats` as:
```python
add_section("task-statement", _task_statement(session))

history_content = _related_history(session) if wave.include_history else ""
add_section("related-history", history_content)

project_context = _read_context_file(repo_root, "project.md")
```

## Files to change

- `cli/src/kcia/waves/definitions.py` — add `include_history: bool = False` to `WaveDefinition`; parse `raw.get("include_history", False)` in `load_waves()`.
- `control-plane/waves/waves.yaml` — add `include_history: true` under the `understanding` wave only.
- `cli/src/kcia/waves/prompts.py` — add `_related_history`/`_history_query`, add the `related-history` section call, add `import json` at top (needed for `json.loads(hit.raw_json)`). While in this file, also translate the one leftover Spanish docstring on `_references_for_wave` ("Filtra por tags...") to English — a straggler from the earlier translation pass, and this repo's English-only policy is now a hard rule (`CLAUDE.md`).
- `tests/test_prompt_composition.py` — the section-order assertion shifts: `names[:4]` becomes `["role", "guardrails", "task-statement", "related-history"]` and `names[4]` becomes `"project-context"` (repo-map moves to index 5). Recompute `PHASE0_UNDERSTANDING_TOKENS`/`PHASE1_UNDERSTANDING_TOKENS` and regenerate `tests/fixtures/prompts/understanding-baseline.md` after implementing (the `melos_session` fixture has no session history logged, so the new section is empty and the baseline diff should be minimal). Also translate the remaining Spanish comments/docstrings in this file (mandatory-English policy).
- `tests/test_optimization_budget.py` — recompute `PHASE0_TASK_TOKENS`/the `assert total <= ...` threshold the same way the earlier translation-caused regression was fixed; translate its Spanish comments.
- New test file `tests/test_history_prompt_integration.py` — the actual behavior proof:
  - Log an entry via `kcia.history.log.append_entry` + `kcia.history.index.sync` into a `melos_session`-style repo, with a title overlapping the session's task text.
  - Assert `build_prompt(get_wave("understanding"), session)` contains `"## Related history"` and the logged title.
  - Assert `build_prompt(get_wave("analysis"), session)` (a wave with `include_history` unset/False) does **not** contain `"## Related history"`.
  - Assert a session with **no** history logged produces no `related-history` content (empty section, no crash) — covers the fresh-repo/no-log-file path.
  - Assert a task whose title contains punctuation that would break a naive FTS5 query (e.g. `"fix: header (overflow)"`) does not crash `build_prompt` — covers `_history_query` sanitization / the try/except.

## Verification

```bash
.venv/bin/pip install -e "./cli[dev]"
.venv/bin/pytest tests/test_history_prompt_integration.py tests/test_prompt_composition.py tests/test_optimization_budget.py -v
.venv/bin/pytest -q   # full suite must stay green
```
Manual check in a scratch repo: log a session with `kcia session log --title "Fix layout overflow" ...`, then start a task with an overlapping title (`kcia task init "layout overflow on profile screen"`) and inspect the composed `understanding` prompt to confirm the `## Related history` section appears with the right entry, and confirm a fresh repo with no prior history produces no such section and no error.

## Status

Implemented. All files listed above were changed as planned: `WaveDefinition.include_history`,
the `related-history` prompt section and its helpers in `prompts.py`, `include_history: true` on
the `understanding` wave in `waves.yaml`, the section-order/token-threshold updates in
`test_prompt_composition.py`/`test_optimization_budget.py`, and the new
`tests/test_history_prompt_integration.py`. Full suite: 239 passed.

**Version bump**: minor (`0.0.1` -> `0.1.0`). This adds a new user-visible capability (the
`understanding` wave now surfaces related past session history) without changing or breaking
any existing command's behavior or interface — no existing prompt section was removed or
renamed, only a new one inserted, and its content is empty (byte-identical output) for any
repo/task with no matching `.ai/history/` entries.
