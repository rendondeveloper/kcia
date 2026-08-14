# task answer: auto-retry the blocked wave

## Analysis

Today, resuming a blocked wave after answering a question takes two commands:

```bash
kcia task answer "<your answer>"
kcia wave retry <blocked_wave_id>
```

This is exactly the sequence `wave.py:_execute` already prints to the user when it
finds a `blocked` wave (`cli/src/kcia/commands/wave.py:190-200`). The user wants
`task answer` to optionally chain straight into the retry, so a question/context
round-trip is one command instead of two.

Relevant code:
- `cli/src/kcia/commands/task.py:200-216` — `task_answer` records the injection via
  `session.add_injection(...)` and exits. It never inspects wave state.
- `cli/src/kcia/commands/wave.py:382-402` — `wave_retry` sets the target wave back to
  `pending` and calls `run_wave(target_id, session, force=True)`, printing a
  completion/failure message.
- `cli/src/kcia/commands/wave.py:190-200` — this is how a blocked wave is detected:
  `session.wave_status(wave.id) == "blocked"` for each wave in `load_waves()`.

The natural fix is a `--retry`/`-r` flag on `task answer`: after recording the
injection, look for the blocked wave the same way `_execute` does, and if one
exists, run the same retry logic in-process (not a subprocess call) and print its
result. If no wave is blocked, print a short note instead of failing, since
`task answer` is also used to "add context" outside of a blocked state.

## Open questions

1. Should auto-retry be opt-in (`--retry`/`-r` flag, default off) or the new
   default behavior of `task answer` (with a `--no-retry` escape hatch)?
   Opt-in preserves today's exact behavior for scripts/muscle memory; default-on
   matches "just do the obvious next thing" but changes existing behavior.
2. If `task answer` is called with no wave blocked (e.g. just adding context
   pre-emptively) and `--retry` is passed, should it silently no-op, print a
   note, or error?
3. Naming: `--retry` (mirrors `wave retry`) vs `--continue` vs `--resume`?

**Answers:**

1. Default-on. The user's intent: `kcia task answer "<context>"` should be the
   single command that both records the context and re-runs the blocked wave
   with it — no second command. This matches the precedent of `kcia wave approve`,
   which already records + continues by default (`--no-run` is the opt-out).
   `task answer` gets a `--no-retry` escape hatch for the old record-only behavior.
   Confirmed: no separate "create a new plan" step is needed beyond the retry —
   `prompts.py:149-152` already folds every recorded injection into the wave's
   prompt before it runs, so retrying the blocked wave (e.g. `understanding`)
   with the injected context *is* re-analyzing with the new context; the wave's
   own output (e.g. an updated plan/analysis file) is what's regenerated, not a
   separate artifact.
2. No wave blocked + retry attempted: print a note (not an error, not silent) —
   e.g. "No wave is blocked; injection recorded for the next wave that runs."
   — since `task answer` is legitimately used to pre-load context before any
   wave has asked a question.
3. `--no-retry` (see answer 1); no new "on" flag needed since retry is now the
   default path.

## Proposed plan

1. In `cli/src/kcia/commands/task.py`, extend `task_answer`:
   - Add `--no-retry` (bool, default `False`) so retry runs unless opted out.
   - After `session.add_injection(...)`, unless `--no-retry` was passed, find the
     blocked wave via `next((w for w in load_waves() if session.wave_status(w.id) == "blocked"), None)`.
   - If found: reuse the retry mechanics from `wave_retry` — set the wave to
     `pending`, call `run_wave(target_id, session, force=True)`, and echo the
     same completion/failure messages `wave_retry` uses today (this re-runs the
     wave's own analysis/output with the injected context folded in, so no
     separate "new plan" step is needed).
   - If not found (no wave blocked): echo
     `"No wave is blocked; injection recorded for the next wave that runs."`
   - If `--no-retry` was passed: echo `"Injection recorded."` as today.
2. Factor the shared "flip blocked wave to pending and run it" logic out of
   `wave_retry` into a small helper (e.g. in `kcia/waves/session.py` or a shared
   spot in `commands/wave.py` imported by `task.py`) so both commands call the
   same code instead of duplicating it.
3. Update `cli/src/kcia/commands/wave.py:190-200`'s printed hint (currently shows
   the two-command sequence) to reflect the new one-command flow once decided.
4. Add/update tests covering: `task answer` (default) with a blocked wave (wave
   retried, output matches `wave retry`'s), `task answer` with no blocked wave
   (note message, no crash), and `task answer --no-retry` (old record-only
   behavior preserved).
5. Bump `VERSION` in `cli/src/kcia/__init__.py` — this is a new capability
   (additive CLI behavior), so minor bump, per CLAUDE.md semver rule.

## Version bump

Applied: **0.3.0** (minor). `task answer` now retries blocked waves by default;
`--no-retry` preserves the old record-only behavior.
