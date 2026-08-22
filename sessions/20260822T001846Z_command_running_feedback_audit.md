# Command running-feedback audit

## Request

Audit every `kcia` CLI command and verify each one shows a visible "running" animation/indicator as soon as it is launched, so the user has client-side confirmation that something is executing.

## Analysis (re-checked against current code)

The CLI has two progress widgets in `cli/src/kcia/waves/progress.py`:

- `WaveProgress` — self-updating status line (spinner + activity + elapsed/tools/tokens) for provider/agent calls. On a TTY it redraws in place. Off a TTY it writes one static `{header} — running` line, unless `periodic_updates=True` (then it reprints a status line every 2s). That flag is **only** set from `kcia work approve`.
- `StepProgress` — lighter spinner for a named git step. On a TTY it animates. **Off a TTY it is silent** — the caller is expected to print the label itself.



### Current usage, by command


| Command                                                                         | File                                         | Long-running work?                                      | Feedback today                                                                                                                                                                                                                                                                        |
| ------------------------------------------------------------------------------- | -------------------------------------------- | ------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `kcia work` (create/continue)                                                   | `commands/work.py`                           | Ticket fetch + wave/agent run                           | `WaveProgress` via `_ProgressReporter`; `periodic_updates=False` (one static "running" line off TTY, then silence until finish)                                                                                                                                                       |
| `kcia work approve`                                                             | `commands/work.py`                           | Wave/agent run after approval                           | `WaveProgress` with `periodic_updates=True` — the only path that keeps updating off a TTY                                                                                                                                                                                             |
| `kcia work fetch`                                                               | `commands/work.py`                           | Ticket fetch (provider call)                            | `WaveProgress` in `_fetch_with_progress`; **no** `periodic_updates`                                                                                                                                                                                                                   |
| `kcia work retry`                                                               | `commands/work.py`                           | Full wave/agent re-run                                  | **No progress at all.** Calls `retry_wave()` → `run_wave(...)` with no `on_event` / `on_wave_start`. Terminal is silent until the result line.                                                                                                                                        |
| `kcia work answer` (retry path)                                                 | `commands/work.py`                           | Same as retry when a wave is blocked                    | Same gap: `retry_wave()` with no reporter                                                                                                                                                                                                                                             |
| `kcia work` other subcommands (`show`, `plan`, `list`, `skip`, `abort`, `logs`) | `commands/work.py`                           | Local reads/writes                                      | none needed                                                                                                                                                                                                                                                                           |
| `kcia done` (push)                                                              | `commands/commit.py`                         | `git push`                                              | `_run_step` → `typer.echo(label)` + `StepProgress`                                                                                                                                                                                                                                    |
| `kcia done` (open PR)                                                           | `commands/commit.py`                         | `git push` + `gh pr create`                             | push via `_run_step`; PR via `typer.echo` + `StepProgress`                                                                                                                                                                                                                            |
| `kcia done` (merge)                                                             | `commands/commit.py` `_finish_gitflow_merge` | fetch, checkout, pull, merge, push, delete local/remote | **Already wrapped.** Every step including `merge_no_ff` goes through `_run_step` → `StepProgress`. The earlier draft of this plan that named `_merge_to_base` is stale — that helper was implemented as `_finish_gitflow_merge` (commit `a5f5e9e`) and already has per-step progress. |
| `kcia branch`                                                                   | `commands/branch.py`                         | Local `create_branch`                                   | none — local/instant                                                                                                                                                                                                                                                                  |
| `kcia init`                                                                     | `commands/init.py`                           | Profile detection walk (depth 4) + template writes      | none                                                                                                                                                                                                                                                                                  |
| `kcia doctor`                                                                   | `commands/doctor.py`                         | Local git-flow + session-history checks                 | none — fast                                                                                                                                                                                                                                                                           |
| `kcia profile`                                                                  | `commands/profile.py`                        | Pack load/copy/validate                                 | none — local; `profile add` still rejects git URLs                                                                                                                                                                                                                                    |
| `kcia mcp` / `kcia agent` / `kcia session`                                      | respective files                             | Local config/history                                    | none — fast                                                                                                                                                                                                                                                                           |
| `kcia ask`, `kcia auth`, `kcia sync`                                            | `commands/_stubs.py`                         | N/A                                                     | stubs                                                                                                                                                                                                                                                                                 |




### Findings (what is actually missing)

1. **Closed — do not re-do:** `kcia done --merge` already shows a label + `StepProgress` for `Merging` {branch}`into`{base}`` (and for fetch/checkout/pull/push/delete). Wrapping `merge_no_ff` again would be a no-op.
2. **Real gap — silent agent runs:** `kcia work retry` and `kcia work answer` (when it retries a blocked wave) run the provider with **no** `WaveProgress`. On both TTY and non-TTY this is a hang-with-no-feedback. This is the only already-implemented, non-trivial path that has zero running indicator.
3. **Non-TTY gap for already-instrumented agent runs:** `kcia work` (create/continue) and `kcia work fetch` use `WaveProgress` but without `periodic_updates`. Off a TTY (Cursor/agent wrapper, pipes) the user sees one "running" line and then nothing until completion — the same class of complaint that `work approve` already fixed. On a real TTY the in-place spinner is already there.
4. **Non-TTY gap for git steps:** `StepProgress` stays silent off a TTY. `kcia done` still prints the step label via `typer.echo` before the subprocess, so there *is* a "something started" line; there is no heartbeat while `git push` / `gh` / merge is in flight. Same pattern as finding 3, milder because the label is at least printed.
5. **Judgment call:** `kcia init` detection walk has no spinner. Local, usually fast; could matter on a large monorepo. Everything else without a spinner is sub-second filesystem/config work; flashing a spinner there is not useful.



## Open questions

1. The merge path already has `StepProgress`. Did you still see silence on `kcia done` (merge/push/PR), or was the command that felt stuck a `kcia work` path (`work`, `retry`, `answer`, `fetch`)? (use  `StepProgress`)
2. Should `kcia work retry` and `kcia work answer` (retry) get the same `WaveProgress` reporter as `kcia work` / `work approve`? Recommended: yes — they are full agent runs with no indicator today. (Yes)
3. Should `periodic_updates=True` (the non-TTY heartbeat already used by `work approve`) also apply to `kcia work`, `work fetch`, `work retry`, and `work answer`? Previous plan scoped it to `approve` only; this audit is the request to widen that. (Yes, periodic updated)
4. Should `kcia init`'s detection phase get a `StepProgress("Detecting profiles…")` line? (yes)
5. Any other command/interaction you've personally seen "hang" with no feedback that isn't in the table? (Not)



## Proposed plan (pending answers)

Default recommendation if questions 2 and 3 are yes and 4 is no:

1. Route `work retry` and `work answer`'s retry through `_execute` (or an equivalent helper that constructs `_ProgressReporter` and passes `on_event` / `on_wave_start` into `run_wave` / `retry_wave`). Do not leave `retry_wave()` as a silent `run_wave(...)` with no callbacks.
2. If question 3 is yes: pass `periodic_updates=True` for those same long-running `work` paths (and `_fetch_with_progress`), not only `work approve`. Keep `StepProgress` as-is unless question 1 says `kcia done` itself felt stuck off a TTY — in that case add a non-TTY periodic mode to `StepProgress` matching `WaveProgress`.
3. Tests: retry/answer must start a `WaveProgress` (assert a "running" or periodic status line before completion). If periodic updates are widened, assert the non-TTY heartbeat on those paths; leave the TTY in-place spinner unchanged.
4. Do **not** re-wrap `_finish_gitflow_merge` / `merge_no_ff`.
5. Bump `VERSION` in `cli/src/kcia/__init__.py` as a **patch** if this is only wiring existing progress into silent callers; **minor** if periodic updates are widened to all agent-running commands (new default behavior off TTY). Record the choice at implementation time.

No other commands require changes under the recommended scope; local/instant commands stay without a spinner.

## Implementation

- **Version**: `0.12.0` (minor). Periodic updates are now the default for every agent-running `work` path, not only `approve`. That is new default behavior off a TTY. `StepProgress` also heartbeats off a TTY, and `kcia init` shows detection as a step.
- `work retry` and `work answer` (retry) go through `_retry_with_progress`, which wires `WaveProgress` into `retry_wave`.
- `kcia work`, `work fetch`, `work retry`, and `work answer` all set `periodic_updates=True`.
- `StepProgress` reprints a status line off a TTY (wait-first, so fast git steps still only show the caller's label).
- `kcia init` prints `Detecting profiles…` and wraps `detect()` in `StepProgress`.
- Merge/`merge_no_ff` was not re-wrapped.