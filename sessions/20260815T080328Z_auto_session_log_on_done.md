# Auto-log the session when `kcia done` finishes

## Analysis

Current flow, from `cli/src/kcia/commands/commit.py:commit_command` (bound to
`kcia done` in `main.py`):

1. Plans and confirms commits.
2. Writes them via `git_commit`.
3. Attaches the written commits to the active `Session` (`.ai/task.yaml`-backed,
   `kcia.waves.session.Session`) if one exists, and calls `session.save()`.
4. If any commit was written, prints a *tip* telling the user to run
   `kcia session log --title ...` themselves to record the work in
   `.ai/history/sessions.jsonl`.

That last step is manual today: `kcia session log` (`commands/session.py`) is a
separate command requiring `--title`, and nothing calls
`kcia.history.log.entry_from_git` / `append_entry` / `index.sync` from
`commit_command`. The user wants step 4 replaced: `kcia done` should call the
session-history log itself, automatically, right after committing, and simply
report success or failure — no separate manual command needed for the common
case.

`kcia session log` as a standalone command should still exist (useful to log a
session without going through `done`, or to add `--decision`/`--file`
overrides), so this is additive to `commit_command`, not a removal from
`session.py`.

### What data is available to build the entry automatically

`log.entry_from_git(repo_root, *, title, summary, decisions, files, commit_sha, task_id)`:

- `title` — no free-text summary is available at `done` time beyond the commit
  subject(s). Use the resolved subject (`resolved_subject`, already computed
  in `commit_command`) as the title. It's already required to be non-empty and
  is what the user/task called the work.
- `summary` — nothing better than "" is available automatically; leave empty
  (matches what a human would leave blank too if not passed explicitly).
- `decisions` — none available automatically; empty list.
- `files` — pass `None` so `entry_from_git` derives it from `changes(repo_root)`
  (post-commit worktree, which will usually be clean, so this reduces to the
  files touched, taken from `written` commits' `planned.paths` instead — see
  open question below).
- `commit_sha` — the last written commit's sha (`written[-1][0]`), or if there
  were multiple commits, is a single `commit_sha` field enough? The dataclass
  only supports one. Use the last commit's sha (the one that closes the task);
  same choice `_open_pr` already makes for the PR title (`written[-1][1]`).
- `task_id` — `session.task.get("id")` when a session exists, else `None`.

`_reject_non_english` in `session.py` guards user-supplied `--title`/`--summary`/
`--decision` strings. `commit_command` already builds `resolved_subject` from
either the CLI arg or `session.task["title"]`, both of which passed through
whatever validation created them — no extra guard is being bypassed by
reusing it as the log title, but for defense in depth the same character
check should still run before writing history, since `done`'s subject could
still come from free-form CLI input.

### Open questions

1. **Files field**: derive from git worktree state (`entry_from_git`'s default,
   via `_files_from_worktree`) after all commits are made, or pass the union of
   `planned.paths` across the just-written commits explicitly? After commits
   land, the worktree is normally clean, so the default would log an empty
   file list — which defeats the point of recording *what changed*. Proposal:
   pass the union of paths from `written`/`commits` explicitly.

   > answer: pass the union of paths from `written`/`commits` explicitly (all marked `modified`).

2. **On log failure**: `session_log` today turns `OSError` into
   `typer.echo(...)` + `typer.Exit(1)`. Inside `commit_command`, the commits
   are already written by the time logging runs. Should a logging failure
   still exit non-zero (commits happened, so this would be surprising post-hoc
   for scripts checking `$?`), or should it only print a one-line
   "session was not saved: <reason>" and exit 0 since the primary action
   (`done`) already succeeded?

   > answer: print `Session was not saved: <reason>` and exit 0 — commits already succeeded.

3. **Opt-out**: does `kcia done` need a flag (e.g. `--no-log`) to skip
   auto-logging, or is it always-on with no way to suppress it? The user's
   request only asked for the report line ("solo se debe de informar que la
   sesión se guardó o no"), not for an opt-out, so default to always-on,
   no flag, unless told otherwise.

   > answer: always-on, no `--no-log` flag.

## Proposed plan

1. In `cli/src/kcia/commands/commit.py`, after commits are written and the
   session's `commits` list is saved (i.e. replacing the current
   `"Tip: run kcia session log..."` block):
   - Build the entry via `kcia.history.log.entry_from_git`, reusing
     `resolved_subject` as `title`, `""` as `summary`, `[]` as `decisions`,
     the union of committed paths as `files` (pending Q1), `written[-1][0]` as
     `commit_sha`, and `session.task.get("id")` (or `None`) as `task_id`.
   - Call `log.append_entry` and `index.sync`, mirroring
     `commands/session.py:session_log`.
   - On success: `typer.echo(f"Session {entry.id} saved.")` (exact wording TBD
     to match existing CLI voice).
   - On failure (`OSError` from `append_entry`, or any indexing exception):
     print `Session was not saved: <reason>` (pending Q2 for exit behavior)
     instead of raising past `done`'s own success.
   - Remove the old "Tip: run `kcia session log --title ...`" line entirely —
     it's now automatic.
2. No changes needed to `cli/src/kcia/history/log.py` or
   `cli/src/kcia/history/index.py` — `entry_from_git`/`append_entry`/
   `index.sync` are reused as-is.
3. `cli/src/kcia/commands/session.py` (`session log` subcommand) stays
   unchanged — still available for manual/standalone logging.
4. Update/add tests near the existing `commit_command` tests (likely
   `tests/test_commit_command.py` or similar — to confirm exact path during
   implementation) to cover: a successful `done` run creates a
   `.ai/history/sessions.jsonl` entry and prints the confirmation line; a
   logging failure (e.g. read-only `.ai/history/`) prints the failure line
   without breaking the already-written commits.
5. Update `cli/src/kcia/main.py`'s `ROOT_HELP` / `commit.py`'s command
   docstring if the auto-logging behavior is worth surfacing in `--help` text
   (likely yes — one line).
6. Bump `VERSION` in `cli/src/kcia/__init__.py` — this is a new capability
   (auto-logging previously required a manual follow-up command), so **minor**
   version bump per semver judgment in CLAUDE.md.

## Version bump

Proposed: **minor** (new user-facing capability: `kcia done` now writes
session history automatically instead of requiring a manual follow-up
command).

**Applied:** `0.5.0` → `0.6.0` (minor — auto session logging on `kcia done`).
