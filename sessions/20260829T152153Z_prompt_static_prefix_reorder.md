# Reduce token cost by making static content a stable, cache-friendly prefix

## Analysis

Generated prompts in this repo come from two composers:

- `cli/src/kcia/waves/prompts.py:build_prompt_with_stats` — assembles wave prompts (understanding, analysis, implementation, etc.).
- `cli/src/kcia/ask/prompt.py:build_ask_prompt` — assembles `kcia ask` prompts.

Prompts are not sent via the Anthropic API directly (no `cache_control` usage anywhere in `cli/src/kcia`); they are handed to a provider CLI as a subprocess (`cli/src/kcia/providers/claude.py`, `cursor.py`, `opencode.py`). This repo cannot set explicit cache breakpoints, but every provider's own caching (Claude Code, Claude API on the provider side, etc.) works on a **stable text prefix**: as long as the leading portion of the prompt is byte-identical to a previous call, that portion can be served from cache instead of reprocessed. If content that changes turn-to-turn is placed *before* large static content, the static content stops being a stable prefix and gets reprocessed (and billed/latency-charged) every time, even though it never changed.

Current section order in `build_prompt_with_stats` (`cli/src/kcia/waves/prompts.py:41-193`):

1. `role` (static per wave/agent)
2. `guardrails` (static)
3. `task-statement` (**dynamic** — per task)
4. `related-history` (**dynamic** — per task)
5. `project-context` (semi-static — changes rarely)
6. `repo-map` (**dynamic** — can change between runs)
7. profile bundles: references + `### Rules` (**large, static per profile** — this is the biggest chunk of most prompts, e.g. `_dart-core` alone renders ~5 reference files plus ~20 rule lines)
8. `task-context` / `ticket-context` / `plan-context` (dynamic)
9. `validation-error` / `injections` (dynamic)
10. `wave-instruction` / `output-format` (static template text, but tiny)

The large static block (step 7, profile bundles) sits **after** three dynamic sections (3, 4, 6). Every wave call within the same task already has a stable task-statement, so 3 alone isn't costly across wave-to-wave calls of one task — but `related-history` (4) and `repo-map` (6) can legitimately differ call to call within a task (history search results depend on the query; repo-map is re-read from the manifest each time), and they currently sit ahead of the profile bundle, breaking its prefix stability whenever they change even slightly.

`build_ask_prompt` (`cli/src/kcia/ask/prompt.py:30-75`) is already closer to correct: `role` → `guardrails` → `project-context` → `repo-map` (dynamic, ahead of nothing large) → history/turns/question. The static `ask.md.j2` instruction text is placed at the very end (`sections["instruction"]`), after all per-turn dynamic content — low cost since it's tiny, but still avoidable.

No code or content that computes prompt *values* needs to change — this is purely a reordering of section assembly, which keeps behavior and content identical and is low-risk.

## Open questions

None — the ordering issue and fix are unambiguous from reading the composer code. Proceeding straight to the plan.

## Plan

1. In `cli/src/kcia/waves/prompts.py::build_prompt_with_stats`, reorder the `add_section` calls (and any code that must run earlier to produce their content) into two groups, preserving current section boundaries and content but changing assembly order:
   - **Static-first group:** `role`, `guardrails`, profile bundles (references + rules; requires resolving `profile_ids` before this point instead of after — the resolution logic itself doesn't need to move, only where its output is appended), `project-context` (only changes when the file is hand-edited, effectively static within a run).
   - **Dynamic-last group (unchanged relative order otherwise):** `task-statement`, `related-history`, `repo-map`, `task-context`, `ticket-context`, `plan-context`, `validation-error`, `injections`.
   - Keep `wave-instruction`, `blocked-protocol`, `output-format` at the end, as today.
   - `stats.sections` (used for budget accounting/telemetry) must keep recording per-section stats in the same set as today — only the join order of `sections` (the list joined into the final prompt) changes, not what `add_section` tracks or drops.
2. In `cli/src/kcia/ask/prompt.py::build_ask_prompt`, move `sections["instruction"]` (static `ask.md.j2` text) to immediately after `sections["guardrails"]`, before any dynamic section, in both the `resume` and full-prompt branches. Keep `sections["question"]` last in both branches (it's the actual user input and should stay last regardless of caching, since it's what the model should attend to most).
3. Do not change `_apply_budget`'s `_DROP_ORDER` (`cli/src/kcia/ask/prompt.py:27`) or `budget.drop_order` (`cli/src/kcia/waves/prompts.py`) — dropped sections must remain governed by relevance/cost, not by prefix position, otherwise budget trimming could start dropping static content that is cheap to keep cached.
4. Add or update tests covering the assembled section order (e.g. asserting the profile bundle text appears before `related-history`/`repo-map`/`task-statement` in the joined prompt) in whichever test file already covers `build_prompt_with_stats`/`build_ask_prompt`, so the ordering invariant doesn't silently regress later.
5. No profile YAML, template `.j2` files, or guardrail docs need to change — this plan only touches the two Python composer functions and their tests.

## Versioning

This is an internal, non-breaking change (no new capability, no behavior change visible to a user beyond potential provider-side cache hits/latency): bump `VERSION` in `cli/src/kcia/__init__.py` as a **patch** release once implemented.

**Implemented:** `0.19.1` → `0.19.2` (patch — prompt section reorder only; no API or user-visible behavior change beyond cache-friendly prefix layout).
