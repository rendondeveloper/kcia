# Per-profile progress lines for multi-profile waves

## Analysis

`bd3dd1e` ("Restore live progress on multi-profile waves after `kcia work
approve`", `0.16.2`) fixed the *silent* case: `_run_multi_profile_wave` now
calls `on_wave_start` once and forwards each profile's provider events into
the shared `WaveProgress` line via `_on_event_for_profile`
(`cli/src/kcia/waves/runner.py`). That line is a single multiplexed status
line: it shows `{wave_id} · {agent} · {provider}/{model} — {activity}`, and
`{activity}` is last-write-wins — whichever profile's thread updated it most
recently. With `backend-dart` and `mobile-flutter` running in the same batch,
the line flips between `backend-dart write.dart` and `mobile-flutter
event_creator.dart` and the user cannot tell both are actually progressing at
once, or which one is stuck.

The predecessor plan
(`sessions/20260822T120811Z_work_approve_progress_multi_profile.md`) already
flagged this and left the door open: *"If you want a different shape than
the single multiplexed status line (for example one reserved TTY line per
profile), say so before implementation."* The user has now asked for exactly
that: always visible feedback that something is happening, and when a wave
runs multiple profiles in parallel, see what each profile is doing
individually rather than one shared/flickering line.

Current building blocks (`cli/src/kcia/waves/progress.py`):

- `WaveProgress` owns one line: a TTY animation thread (`_animate`, redraws
  in place with `\r`) or an off-TTY periodic reprint (`_animate_periodic`,
  one full `\n`-terminated line every `periodic_tick` seconds). Both read
  mutable state (`_activity`, `_tool_calls`, `_tokens`, `_files_written`)
  guarded by `self._lock`.
- `_ProgressReporter` (`cli/src/kcia/commands/work.py`) owns the single
  `WaveProgress` instance for the wave currently running and is the
  `on_wave_start` / `on_event` target passed into `run_wave`.
- `_on_event_for_profile` (`runner.py`) already tags each event with its
  `profile_id` before forwarding it — the tag is currently only used to
  prefix the activity text baked into `ToolCallStart.name` /
  `FileRead|FileWrite.path`; the reporter has no notion of "this event
  belongs to profile X" as structured data.

Multi-profile waves are `implementation` and `documentation-final`
(`_MULTI_PROFILE_WAVES` in `runner.py`), run through `_run_multi_profile_wave`
with a `ThreadPoolExecutor`, profiles within a batch running concurrently,
batches themselves sequential (dependency order from `execution_batches`).
The number of concurrent profiles in a batch is known up front (`to_run` /
`futures` in `_run_multi_profile_wave`) before submission.

### "multisessions o solo una"

Read as: this is about progress *display* — one shared status line vs. one
line per profile — not about spawning multiple `kcia work` sessions or
multiple provider *sessions* per profile. Provider calls per profile already
run as independent threads/requests (`run_wave_for_profile` per
`ProfileExecution`); nothing here changes that. If a different meaning was
intended (e.g. running multiple `kcia work` tasks concurrently), say so under
Open questions.

## Open questions

1. **Rendering shape on a TTY.** Proposed: reserve one terminal line per
   profile directly under the wave header, redrawn in place (cursor moves up
   N lines, rewrites, moves back down), e.g.:

   ```
   implementation · builder · anthropic/claude-sonnet-5
     ⠋ backend-dart   writing route_notification_policy.dart · 12 tools · 4.2k tok
     ⠙ mobile-flutter reading event_creator/state.dart · 8 tools · 3.1k tok
   ```

   Confirm this layout is what "ver que hace cada perfil" means, or say if
   you want something else (e.g. one line per profile with no shared header,
   or a compact single-line summary like `2/2 profiles running` with detail
   only in `kcia work list`/logs).

   **Answer:** Confirmed. Wave header plus one in-place line per profile.

2. **Off-TTY behavior** (CI, piped output, `| tee`). Proposed: keep the
   existing periodic reprint, but emit one line per profile per tick instead
   of one shared line, so logs stay grep-able per profile:
   `backend-dart — writing … · 12 tools · 4.2k tok` /
   `mobile-flutter — reading … · 8 tools · 3.1k tok`, each on its own line
   every `periodic_tick` (2s). Confirm this is acceptable log volume, or if
   off-TTY should stay a single coarser line (e.g. `2 profiles running`)
   and only the TTY path gets per-profile detail.

   **Answer:** Confirmed. One line per profile per periodic tick.

3. **`documentation-final`'s trailing integration check**
   (`_run_integration_check`, `runner.py` ~line 1037) runs *after* all
   profiles finish and passes the original single-profile `on_event`
   straight through — no `profile_id`. Proposed: it keeps using the plain
   single-line `WaveProgress` (no profile column) since it is not itself
   per-profile work. Confirm.

   **Answer:** Confirmed. Integration check stays on a single `WaveProgress` line.

## Proposed plan

Scope: `cli/src/kcia/waves/progress.py`, `cli/src/kcia/waves/runner.py`,
`cli/src/kcia/commands/work.py`, `tests/test_progress.py`,
`tests/test_multi_profile_runner.py`. No control-plane changes.

1. **Structured per-profile events.** Change `_on_event_for_profile` to stop
   baking the profile id into `ToolCallStart.name` / `FileRead|FileWrite.path`
   strings and instead have the reporter learn the profile id out-of-band —
   either a `profile_id` field threaded through a wrapper event, or
   `_ProgressReporter`/`WaveProgress` gaining a per-profile `handle(event,
   profile_id=...)` entry point. Keep `StreamEvent` payloads unprefixed so
   downstream consumers (logs, `kcia work logs`) stay clean.

2. **`MultiWaveProgress` (new class in `progress.py`)**, replacing
   `WaveProgress` for multi-profile waves only:
   - Constructed with the wave header plus the known list of profile ids for
     the batch about to run (`_run_multi_profile_wave` already has this via
     `to_run` before submitting futures).
   - Owns one `_activity`/`_tool_calls`/`_tokens`/`_files_written` slot per
     profile id, each guarded by the existing lock.
   - TTY: one animation thread redraws all N lines in place per tick
     (per open question 1).
   - Off-TTY: one animation thread reprints N lines per `periodic_tick`
     (per open question 2).
   - `finish()` reports a per-profile summary line each (duration, tool
     calls, files written, tokens), mirroring `WaveProgress.finish()`'s
     single-line summary today.
   - Single-profile waves keep using plain `WaveProgress` unchanged — no
     behavior change there.

3. **Wire it into `_run_multi_profile_wave`.** Replace the
   `on_wave_start(wave, agent)` call with a call that also passes the
   profile id list for the batch about to run (batches run sequentially, so
   the reporter is re-scoped — or the line set grows/shrinks — per batch).
   `_on_event_for_profile` starts passing `profile_id` through so the
   reporter can route each event to the right line.

4. **`_ProgressReporter` in `work.py`** gains a second constructor path (or a
   sibling class) that hands back a `MultiWaveProgress` when `run_wave`
   reports multiple profiles, still driven by the same `on_wave_start` /
   `on_event` / `finish` contract `_run_loop` and `_retry_with_progress`
   already use, so no caller-side branching is needed beyond wiring the new
   signature.

5. **Tests**: extend `tests/test_progress.py` with `MultiWaveProgress`
   coverage (per-profile line rendering on/off TTY, finish summary per
   profile) and `tests/test_multi_profile_runner.py` to assert
   `_run_multi_profile_wave` starts the multi-line reporter with the correct
   profile id set and routes each profile's events to its own line.

### Versioning

**Patch** (`0.16.2` → `0.16.3`). Progress-line rendering only; no change to
`WaveResult` or session data shapes. Recorded at implementation.
