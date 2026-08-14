# Merge `kcia task` and `kcia wave` into `kcia work`

## Request (as understood)

`kcia start` already replaced `kcia init` and stays as-is — no change needed there.

The commands `kcia task init "<ticket or text>"` and `kcia wave run` should be merged into
a single command group named `kcia work`, so that one invocation both creates the task
(`task init`'s job) and drives the wave pipeline (`wave run`'s job).

Additionally, `kcia task show` becomes `kcia work show`.

## Current state

- `kcia task` (`cli/src/kcia/commands/task.py`) — `Typer` sub-app with: `init`, `show`,
  `fetch`, `answer` (alias `inject`), `abort`.
- `kcia wave` (`cli/src/kcia/commands/wave.py`) — `Typer` sub-app with: `list`, `run`,
  `approve`, `plan`, `retry`, `skip`, `logs`.
- Both operate on the same `Session` (`kcia.waves.session.Session`), stored at
  `.ai/local/session.json`. `task init` creates it; `wave run` reads/advances it. There is
  no structural reason they can't live under one Typer app.
- `main.py` registers them as `app.add_typer(task_app, name="task")` and
  `app.add_typer(wave_app, name="wave")`.
- `kcia task init` already calls `_fetch_ticket` at the end (ticket mode); it does not call
  into wave running today. `kcia wave run` refuses to start if `Session.load` fails
  ("No active task. Run `kcia task init` first.").
- README.md and CHANGELOG.md document `kcia task ...` / `kcia wave ...` extensively
  (README lines 42–1210, per earlier grep) — these need updating to match.
- `ROOT_HELP` in `main.py` lists `kcia task init "fix the overflow"` and `kcia wave run` as
  the two common-command lines.

## Proposed plan

1. Create `cli/src/kcia/commands/work.py` with a single Typer app `app` (name `work`,
   `no_args_is_help=True` is wrong here since the bare command *does* something — see open
   question 1).
2. Move all subcommands currently under `task` and `wave` into this one app, keeping their
   existing names except where they collide:
   - From `task.py`: `show` → `work show`, `fetch` → `work fetch`, `answer`/`inject` →
     `work answer`/`work inject`, `abort` → `work abort`.
   - From `wave.py`: `list` → `work list`, `approve` → `work approve`, `plan` → `work plan`,
     `retry` → `work retry`, `skip` → `work skip`, `logs` → `work logs`.
   - `task init` and `wave run` (including its `wave_id` argument, now `--wave <id>`)
     collapse into the single bare `work` command (see below) — not `work init` / `work
     run` — per the request, "un solo comando" that takes the ticket-or-text argument and
     does both, with no duplicate second entry point into the pipeline.
3. The bare `kcia work "<ticket or text>"` command:
   - Runs the existing `task_init` body first (validates repo, scope, classifies input,
     creates the `Session`, fetches the ticket body if applicable).
   - Then falls straight into the existing `wave_run` body (`_load_runnable_session` +
     `_execute`), reusing the just-created session instead of reloading — so the same
     `--yes`/`--quiet`/`--force`/`--until` options from `wave run` need to be exposed on
     `work` too, alongside `--ticket`/`--prompt`/`--profile`/`--scope`/`--fetch` from
     `task init`.
   - If a task is already active in the repo (`Session.load` succeeds), `kcia work` with no
     text argument should behave like today's `kcia wave run` (resume/continue the pipeline)
     rather than erroring — this covers the case where a user runs `kcia work` again after
     being blocked, mirroring how `wave run` currently works. Needs confirmation (open
     question 2).
4. Delete `task.py` and `wave.py`, or keep them as thin deprecated aliases — needs a
   decision (open question 3), consistent with how `kcia init` → `kcia start` was handled
   in commit c725295 (that rename appears to have been a clean cutover, not an alias).
5. Update `main.py`: remove `task_app`/`wave_app` imports and registrations, add
   `app.add_typer(work_app, name="work")` (or register the bare command directly if Typer
   allows a callback + subcommands on the same app — need to confirm bare-arg command
   coexists with subcommands like `work show`, `work list`, etc., which it does via
   `invoke_without_command=True` on the group's `@app.callback`, same pattern already used
   in `_stubs.py`). Update `ROOT_HELP` to show `kcia work "fix the overflow"` instead of the
   two separate lines.
6. Update all references across README.md and CHANGELOG.md (`kcia task init`, `kcia task
   show`, `kcia task fetch`, `kcia task answer`, `kcia task abort`, `kcia wave list`, `kcia
   wave run`, `kcia wave approve`, `kcia wave plan`, `kcia wave retry`, `kcia wave skip`,
   `kcia wave logs`) to their `kcia work ...` equivalents, including the ~40 occurrences
   found across README.md (mermaid diagram labels, the command table, the walkthrough
   transcript, the guardrail/injection docs, the domain-model glossary).
7. Update/rename any tests referencing `task` or `wave` Typer sub-apps (need to locate them
   under `tests/` before implementation; not enumerated yet since this is the planning pass
   only).
8. Bump `VERSION` in `cli/src/kcia/__init__.py` (mandatory per CLAUDE.md). This is a
   **breaking CLI change** (command names change, `task init` and `wave run` cease to exist
   as separate commands) — proposed as a **major** version bump, same category as the prior
   `init`→`start`/`commit`→`done` rename in c725295, which bumped to 0.2.0. This plan would
   bump to the next major (e.g. 0.3.0 → confirm current VERSION at implementation time and
   choose accordingly), recorded here once decided.

## Decisions (from user follow-up)

- All `wave` subcommands are renamed to live under `work` too, so the whole surface is
  homologated under one prefix — no `task`/`wave` split anywhere.
- **No separate `work run` subcommand.** The point of the merge is to simplify the flow, so
  a second entry point that duplicates the bare command defeats it. There is exactly **one**
  command that drives the pipeline: `kcia work [text] [options]`.
  - With `text`: create the task if none is active, then run (today's `task init` +
    `wave run` combined).
  - Without `text`: continue the active session's pipeline — "next pending, then keep
    going" (today's bare `wave run`).
  - Targeting a single wave by id (today's `wave run <wave_id>`) is folded into `--wave
    <id>`, an option on the same bare command, rather than a distinct subcommand or a
    second positional that would be ambiguous with the ticket/prompt `text` argument:
    `kcia work --wave understanding`.

## Final command surface

- `kcia work ["<ticket or text>"] [--wave <id>] [--until] [--force] [--quiet] [--yes]
  [--ticket] [--prompt] [--profile] [--scope] [--fetch/--no-fetch]` — the single entry point
  covering create+run, resume+run, and run-one-wave. Union of every option `task init` and
  `wave run` had.
- `kcia work show [--json]` — was `task show`.
- `kcia work fetch` — was `task fetch`.
- `kcia work answer` / `kcia work inject` (hidden alias) — was `task answer`/`task inject`.
- `kcia work abort` — was `task abort`.
- `kcia work list` — was `wave list`.
- `kcia work approve` — was `wave approve`.
- `kcia work plan` — was `wave plan`.
- `kcia work retry [wave_id]` — was `wave retry`.
- `kcia work skip <wave_id> --reason` — was `wave skip`.
- `kcia work logs <wave_id>` — was `wave logs`.

This makes the bare `kcia work ["<text>"]` command the single entry point into the pipeline;
everything else is a 1:1 remount of the old `task`/`wave` subcommands under the `work`
prefix (except `wave run`, which is folded into `--wave` on the bare command).

## Decisions (final)

1. **Active task + new text:** preserve prior `task init` behavior — `Session.create` overwrites
   `.ai/local/session.json` silently (no new guard added).
2. **Back-compat:** clean cutover — `task.py`/`wave.py` removed, no deprecated aliases (same
   as `init`→`start` in c725295).

## Status

**Implemented.** Version bumped **0.3.0 → 0.4.1** (major semver: breaking CLI rename of the
`task`/`wave` command surface; 0.4.1 drops the interim `work run` subcommand in favour of
`--wave` on the bare command, per plan update).
