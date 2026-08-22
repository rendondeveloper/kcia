# Restore live progress on `kcia work approve` (multi-profile waves)

## Analysis

This is the same user-visible symptom as
`sessions/20260821134515_work_approve_progress_feedback.md` (`0.9.0`) and
the later widening in `sessions/20260822T001846Z_command_running_feedback_audit.md`
(`0.12.0`): after `kcia work approve`, the terminal sits silent until
`All waves completed in …`. The first three waves (`understanding`, `analysis`,
`documentation-init`) still print the live status line with elapsed time,
tools, and tokens.

The previous fixes are still in place and still work:

- `work_approve` calls `_execute(..., periodic_updates=True)`
  (`cli/src/kcia/commands/work.py`).
- `_run_loop` passes `on_event=reporter.handle` and
  `on_wave_start=reporter.start` into `run_wave`.
- `WaveProgress` still animates on a TTY and reprints a status line every 2s
  off a TTY when `periodic_updates=True`.

What changed since those plans is the **implementation path**, not the
progress widget.

`implementation` and `documentation-final` are in `_MULTI_PROFILE_WAVES`
(`cli/src/kcia/waves/runner.py`). When `plan.md` contains a parsed `execution:`
block, `run_wave` does **not** take the serial path that starts `WaveProgress`.
It returns through `_run_multi_profile_wave` instead.

That function:

1. Accepts `on_wave_start` and `on_event` but **never calls `on_wave_start`**.
   `_ProgressReporter.start()` therefore never runs. No spinner, no periodic
   heartbeat, no `{header} — running` line.
2. Hard-codes `on_event=None` when submitting `run_wave_for_profile` (around
   line 959), with the comment *"Parallel fan-out currently disables live streaming
   progress to avoid terminal line collisions."*
3. `_run_loop` only calls `reporter.finish()` **after** `run_wave` returns.
   With `_current is None`, that finish is a no-op. The user sees
   `Approved 'implementation'.` then, many minutes later,
   `All waves completed`.

That matches the captured terminal: the planner waves (serial `run_wave`)
showed progress; the ~17 minutes after approve cover `implementation` and
`documentation-final` on the silent multi-profile path.

This is not a TTY vs non-TTY regression. Even on a real IDE terminal the
reporter never starts, so `isatty()` does not matter.

The collision comment is also the wrong diagnosis for the current widget.
`WaveProgress.handle` only mutates in-memory state under a lock; the
animation thread is the only writer to the stream. Forwarding the same
`on_event` into several profile threads would not interleave `\r` lines.
The original multi-profile plan
(`sessions/20260820T055909Z_parallel_multi_profile_orchestration.md` §5)
already asked for a multi-line progress display; that piece was never
built, and the runner disabled progress entirely instead.

Single-profile repos are affected too whenever the planner writes an
`execution:` block (the common case after the multi-profile work). Only a
missing/empty block falls back to serial `run_wave` and keeps the old
progress.

## Open questions

None that block the fix. Design below follows the user's earlier answers:
any visible "the model is building" indicator is enough; do **not** emit a
wall of per-event or per-profile scrolling lines.

If you want a different shape than the single multiplexed status line
(for example one reserved TTY line per profile), say so under this
heading before implementation.

## Proposed plan

Scope: `cli/src/kcia/waves/runner.py`, `cli/src/kcia/commands/work.py` only
if the reporter needs a profile-aware header, `tests/test_progress.py` and
`tests/test_multi_profile_runner.py`. No control-plane changes.

### 1. Start the existing reporter for multi-profile waves

In `_run_multi_profile_wave`, after validation and before the thread pool:

- Resolve the wave + builder agent the same way `run_wave` does.
- If `on_wave_start` is not `None`, call `on_wave_start(wave, agent)` once
  for the wave (not per profile). `_ProgressReporter.start()` already finishes
  any previous line, so this is safe from `_run_loop`.

That alone restores the spinner / 2s heartbeat for the full duration of
`implementation` and `documentation-final`, including validation retries and
the documentation-final integration check.

### 2. Forward `on_event` into each profile run

Replace the hard-coded `on_event=None` with the `on_event` already passed
into `_run_multi_profile_wave`. `run_wave_for_profile` already accepts it and
hands it to `call_provider`.

Do **not** give each profile its own `WaveProgress`. One reporter, one
status line, last event wins. `handle()` is already lock-protected, so
parallel tool events from two builders are safe.

Optional, small UX improvement (do this unless it grows the change): wrap
the shared handler so the activity string is prefixed with the profile id
(`backend-dart Read: …`). Keep it as a thin wrapper around `on_event` in
`_run_multi_profile_wave`; do not add a third progress class.

Do **not** implement N in-place TTY lines. That needs reserved rows / cursor
addressing, would scroll off a TTY when `periodic_updates` reprints, and
conflicts with the "contained indicator" preference.

### 3. Tests

- Multi-profile `run_wave("implementation")` with a fake provider that emits
  a `ToolCallStart` (or `FileWrite`) **must** call `on_wave_start` once and
  forward that event to `on_event`. Today this would fail: start is never
  called and `on_event` is dropped.
- Same assertion for a one-profile `execution:` block (the sport_monitor
  shape): still the multi-profile path, still must show progress.
- Existing serial `test_run_wave` progress test stays the source of truth
  for the non-execution-block path.
- No new requirement to assert the exact spinner glyphs; wiring of the
  callbacks is the contract.

### 4. Out of scope

- Replacing `_ProgressReporter` with a multi-line widget.
- Changing `WaveProgress` periodic interval or TTY vs non-TTY behavior.
- `kcia work list` / `show` per-profile status lines (separate gap from
  the original multi-profile plan).

## Version bump

**Patch** (`0.16.1` → `0.16.2`), recorded at implementation. Restores progress
serial waves already had; not a new capability and no public CLI change.
