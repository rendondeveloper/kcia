# `kcia work abort` clears the context folder

## Request

When `kcia work abort` runs, it should delete the context files left behind by the previous
task — `plan.md`, `milestones.md`, `decisions.md`, `current.md` — so the next task does not
start on top of stale work. The user confirmed: **delete the files** (do not archive them).

## Analysis

### Current behaviour

`Session.abort()` (`cli/src/kcia/waves/session.py:340`) does only two things:

```python
def abort(self) -> None:
    close_cycle(self.repo_root)
    path = session_path(self.repo_root)
    if path.is_file():
        path.unlink()
```

It closes the git cycle and removes `.ai/local/session.yaml`. Everything the waves wrote
into `.ai/context/` survives, so a later `kcia work "<something else>"` runs with the old
`plan.md` / `current.md` / `decisions.md` still on disk. Wave prompts read those files
directly (`waves/prompts.py:174-190`, `_read_context_file`), and
`plan_metadata.load_plan_metadata` (`waves/plan_metadata.py:110-121`) reads `plan.md` plus
`decisions.md`, so stale context is not inert — it is fed to the next run's planner.

### What the waves write into `.ai/context/`

From `control-plane/waves/waves.yaml`:

| wave | writes |
| --- | --- |
| understanding | `.ai/context/task.md` |
| analysis | `.ai/context/plan.md` |
| documentation-init | `.ai/context/current.md`, `.ai/context/decisions.md` |
| documentation-final | `.ai/context/milestones.md` |

Plus two files not produced by a wave template:

- `.ai/context/ticket.md` — written by `integrations/tickets.py` in ticket mode; it is
  per-task too (it holds the fetched Jira issue), and re-fetchable with `kcia work fetch`.
- `.ai/context/project.md` — written by `project_index.py`; this one is **repo-level**, not
  per-task, and must never be deleted by abort.
- `.ai/context/milestones-<profile_id>.md` — per-profile chunks written by
  `runner._merge_documentation_final_milestones` (`waves/runner.py:824-838`) before being
  merged into `milestones.md`. These are per-task leftovers and should go too.

### Scope decision

Delete the per-task context files, keep the repo-level ones. Concretely, abort removes:

- `plan.md`
- `milestones.md` and any `milestones-*.md` chunks
- `decisions.md`
- `current.md`
- `task.md`
- `ticket.md`

and leaves `project.md` (and anything else in `.ai/context/` not in that list) untouched.
Rationale: `task.md`/`ticket.md` are as much "the previous task's work" as `plan.md` is —
leaving them would still let the next `understanding` wave inherit the aborted task's framing,
which is exactly the bug being fixed. The user named four files, but the intent ("clean the
previous work") covers these two by the same argument; this is called out as an open question
below in case they want abort limited to the four named files.

`.ai/context/` is gitignored and regenerable (per CLAUDE.md, "Generated repository state"),
so deleting these files loses nothing a person hand-authored.

### Non-goals

- `kcia done` is **not** changed. `done` commits the task; whether it should also wipe context
  is a separate question and not part of this request.
- Source files the implementation wave wrote stay on disk (README:338 documents this, and it
  stays true — only `.ai/context/` entries are removed).

## Open questions

1. Should abort also delete `task.md` and `ticket.md`, as proposed above? (Default taken in
   this plan: **yes**. Answer "only the four named files" here to narrow it.)
2. Should abort print which files it removed, or stay silent on `Task aborted.`?
   (Default taken: print a one-line summary, e.g. `Task aborted. Cleared 4 context file(s).`,
   because abort is otherwise indistinguishable from a no-op.)

## Plan

1. **`cli/src/kcia/waves/session.py`**
   - Add a module-level constant listing the per-task context filenames, next to
     `context_dir()`:
     ```python
     TASK_CONTEXT_FILES = (
         "task.md",
         "ticket.md",
         "plan.md",
         "decisions.md",
         "current.md",
         "milestones.md",
     )
     ```
     Document in its docstring/comment that `project.md` is deliberately excluded because it
     is repo-level, not per-task.
   - Add `clear_task_context(repo_root: Path) -> list[str]` that unlinks each of those files
     when present, plus every `milestones-*.md` glob match, and returns the sorted names of
     what it actually removed. Use `Path.unlink(missing_ok=True)` and skip anything that is
     not a regular file (a directory named `plan.md` is not ours to delete).
   - Call it from `Session.abort()`, after `close_cycle()` and the session-file unlink, and
     have `abort()` return the list of removed names so the command can report them.

2. **`cli/src/kcia/commands/work.py` (`work_abort`, line 793)**
   - Capture `removed = session.abort()` and echo
     `Task aborted.` followed by `Cleared N context file(s).` when `removed` is non-empty.
     Keep the plain `Task aborted.` when nothing was cleared.

3. **Tests — `tests/test_git_cycle.py`**
   - Extend `test_abort_closes_cycle` or add `test_abort_clears_task_context`: create a
     session, write every file in `TASK_CONTEXT_FILES` plus `milestones-flutter.md` and
     `project.md` into `.ai/context/`, call `session.abort()`, assert the task files and the
     milestones chunk are gone and `project.md` survives.
   - Add a case where `.ai/context/` holds none of these files, asserting `abort()` still
     succeeds and returns an empty list (abort must never fail on a half-built task).

4. **Docs**
   - `README.md:338` — change the `kcia work abort` row to say the task's `.ai/context/`
     files are cleared while the source files it wrote stay on disk. Check README:527 and
     README:707 for the same wording and update them if they imply context is kept.
   - `CHANGELOG.md` — add an entry under the unreleased section.

5. **Version bump** — `cli/src/kcia/__init__.py:VERSION`, **minor**. This changes what an
   existing command does to on-disk state (new behaviour users will notice), but it does not
   break any documented contract or CLI signature: `.ai/context/` is declared regenerable
   output, and no flag, argument, or exit code changes. A behaviour addition on a stable
   interface is a minor bump, not a major one.

## Verification

```bash
.venv/bin/pytest tests/test_git_cycle.py
.venv/bin/pytest
```

## Implementation notes

- Completed 2026-09-10.
- CLI `VERSION` bumped **0.21.1 → 0.22.0** (minor: `work abort` now removes per-task
  context files from disk).
- Open questions resolved with plan defaults: also delete `task.md` and `ticket.md`; print
  `Cleared N context file(s).` when files were removed.
- Files touched: `waves/session.py`, `commands/work.py`, `tests/test_git_cycle.py`,
  `README.md`, `CHANGELOG.md`.
