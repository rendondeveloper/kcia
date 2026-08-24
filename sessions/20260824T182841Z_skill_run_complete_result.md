# Richer `kcia skill` run result (what actually happened)

The screenshot is `kcia skill --backend --deploy` in sport_monitor. After the spinner the terminal only shows:

```text
skill:backend/deploy · builder · cursor/composer-2.5 — completed (10s, 0 tool calls)
```

No Cloud Function names, no URLs, no `SKILL_OK:` line. Exit code of that run was **1**. The matching log `.ai/local/runs/skill-backend-deploy-20260824T181030Z.md` is **empty** (only the `.prompt.md` has content).

kcia is generic: it must not parse Firebase / Cloud Functions. The deploy skill already tells the builder to name the functions. The gap is that **the command does not surface a real result**, and the job prompt asks for a **short** `SKILL_OK:` summary.

## Analysis

### What the user asked for

After `kcia skill` (the command that runs a cataloged skill), the terminal should say **what the skill actually did** — for this skill, **which Cloud Functions were deployed**.

### What happens today

1. `build_skill_prompt` (`cli/src/kcia/skills/runner.py`) tells the builder to end with `SKILL_OK: <short summary>`. That fights the skill file, which already says to report selected functions, why, URLs, and success/failure.
2. `run_cataloged_skill` always prints builder stdout after the spinner (`commands/skill.py`). If stdout is empty, **nothing is printed**. There is no fallback like “no output” or “missing `SKILL_OK:`”.
3. `WaveProgress.__exit__` marks **completed** unless an exception was raised. A missing `SKILL_OK:` still returns exit 1, but the status line still says **completed**. That is exactly the screenshot: it looks like success and reports **0 tool calls**.
4. The Cursor adapter (`cli/src/kcia/providers/cursor.py`) only maps `text_delta` / `message` / `end`/`done`. Cursor `stream-json` may use other event types (`result`, `assistant`, tool calls). If those are ignored, `output_text` stays empty, tool-call count stays 0, and the user never sees the report — even if the model wrote one.

The sport_monitor skill (`.cursor/skills/deploy-changed-cloud-functions/SKILL.md` / `.ai/skills/...`) is **out of scope** for this repo. Do not edit it. Once kcia prints a complete result, that skill’s own “list the functions” instructions can show up.

### What this is not

- Not a Firebase-specific parser.
- Not a sixth wave.
- Not a change to register/list/remove.

## Open questions

None that block a first implementation. Binding choices below (override in this file if wrong):

1. **Generic result, not Cloud Functions-only.** The prompt must demand concrete outcomes (names of things deployed, created, skipped, URLs, environment). A deploy skill then lists functions; another skill lists something else.
2. **Always print a result after the spinner.** Full builder stdout when present. If empty or missing `SKILL_OK:`, print an explicit English failure (do not go silent).
3. **Status line must match the exit code.** Missing `SKILL_OK:` / empty output / provider non-zero → `failed`, not `completed`.
4. **Cursor capture is in scope** only as far as needed for the result to appear: if `stream-json` events are dropped, map the documented Cursor event types so `output_text` and tool-call counts are real. Confirm against `cursor-agent` help / a captured fixture; do not invent event names.

## Proposed plan

Do not implement until this plan is confirmed.

### 1. Job prompt (`build_skill_prompt`)

Replace “short summary” with a complete user-facing result, still ending in one marker line:

- Before the marker: a complete report of what the skill did, in the language the skill asks for. Must include **concrete names** of resources that changed (deployed functions, services, files, URLs), the **target** (project / env / alias), and anything **skipped** or failed. Vague “deployed successfully” is not enough.
- Last line still exactly one of:
  - `SKILL_OK: <same facts, compact>` — names must appear on this line too (e.g. `SKILL_OK: deployed catalog-route-v2, route-route-v2 to dev`).
  - `BLOCKED: <reason>`
- Do not emit both markers.

Keep extra argv forwarding as it is.

### 2. Print the result (`_run_skill` / `run_cataloged_skill`)

After `WaveProgress` finishes:

- If stdout is non-empty: print it (today’s behavior).
- If stdout is empty: print a clear failure, e.g. `skill:backend/deploy produced no output.`
- If exit is 1 because `SKILL_OK:` is missing: say that in one line (in addition to any stdout).
- `BLOCKED:` still exit 2; print the blocked text.

Do not copy output into `.ai/context/`. Keep writing `.ai/local/runs/skill-*.md` as today.

### 3. Progress line vs exit code

`WaveProgress` currently finishes as **completed** unless the `with` block raises. Skill runs that return 1 or 2 must finish as **failed**.

Preferred: after `call_provider`, decide the skill exit code, then `progress.finish(failed=exit_code != 0)` (avoid double-finish from `__exit__`). Same pattern as `kcia work` reporters that call `finish(failed=...)`.

A 10-second run with 0 tool calls and no stdout must not look like success.

### 4. Cursor stream (only if still empty)

If tests or a live `cursor-agent --print --output-format stream-json` fixture show that result text / tool calls are on event types the adapter ignores, extend `CursorAdapter.parse_stream_line` so:

- assistant/result text lands in `final_text` / `TextDelta` (so `output_text` is non-empty);
- tool-call events increment `tool_calls`.

Add adapter tests with a small fixture. Do not change Claude/OpenCode unless the same empty-output bug is proven there.

### 5. Tests

- Prompt contains the complete-result instructions (not “short summary”); still requires `SKILL_OK:` / `BLOCKED:`.
- Run with stdout `SKILL_OK: deployed catalog-route-v2, route-route-v2 to dev` → printed, exit 0.
- Empty stdout → printed failure message, exit 1, progress **failed**.
- Missing `SKILL_OK:` → exit 1, progress **failed**, message mentions the missing marker.
- `BLOCKED:` still exit 2.
- Cursor parse fixture (if step 4 is needed).

### 6. Docs

README skill section: a successful run prints the builder report (including the concrete `SKILL_OK:` facts), not only the spinner line.

### 7. Version

**Patch** `0.18.0` → `0.18.1`. Existing `kcia skill` command; this is result reporting, not a new capability.

## Semver (mandatory after implementation)

- Current before implementation: `0.18.0`
- Implemented: **patch** `0.18.1` — richer and reliable skill-run output; no new command surface.
