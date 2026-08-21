# Post-commit workflow behavior configured at `kcia init`, executed by `kcia done`

## Request

The merge-vs-PR choice for gitflow repos is decided once at `kcia init` time (not interactively inside `kcia done`). Non-gitflow (`current-branch`) repos keep the current default: `kcia done` pushes the commit. `kcia done` executes the configured behavior automatically and reports what happened via progress/step output, without asking anything.

## Analysis (current state)

- `kcia done` is `commit_command` in `cli/src/kcia/commands/commit.py:133-249`, registered as `done` in `cli/src/kcia/main.py:44-47`.
- Today, after committing (gated by `typer.confirm("Commit this?", default=False)` unless `--yes`), it closes the work cycle and deletes the session file. Push (`--push`) and PR (`--pr`, via `gh pr create` in `_open_pr`, lines 116-130) are **opt-in flags only** — nothing happens post-commit unless those flags were passed.
- Gitflow vs. current-branch model is already modeled: `cli/src/kcia/git/flow.py` (`GitFlow` dataclass, `load_flow`/`save_flow`, `detect_base_branch`, `detect_branches`, `branch_name`), config persisted at `.ai/local/git.yaml`, chosen once at `kcia init` (`--gitflow/--no-gitflow`, `init.py` lines ~76-78). This is where the new `on_done` setting is added.
- `git/cycle.py` tracks the open work-cycle (`.ai/local/cycle.json`) while gitflow is active; `close_cycle` already runs in `commit.py` before any push/PR logic today.
- gh CLI availability is already detected via `gh_available()`/`GH_BIN` in `git/repo.py`, used by `_open_pr`. No GitPython — all git ops go through `subprocess` in `git/repo.py`.
- The established interactive-prompt convention in this codebase is plain `typer.confirm(...)` / `typer.prompt(...)` (used in `commit.py:196`, `branch.py:88,192`, `init.py:248,265,273,331`) — the new init-time question follows this convention.
- Tests `tests/test_git_cycle.py` and `tests/test_gitflow_config.py` are currently modified/uncommitted in the working tree — not part of this request and will not be swept into this plan's commit.
- No `git-flow` extension dependency exists or is implied — kcia's "gitflow" is its own lightweight branch-naming/base-branch model.
- `VERSION` is currently `0.10.0` (`cli/src/kcia/__init__.py:3`).

## Decisions (answers to open questions)

1. **Field/default**: `GitFlow` gets `on_done: Literal["pr", "merge"] = "pr"` (meaningful only when `model == GITFLOW`). Default is `"pr"`.
2. **Prompt timing**: the `on_done` question is asked immediately after the user selects gitflow in the `kcia init` flow (interactive path only).
3. **Non-interactive control**: `kcia init` gets an `--on-done={pr,merge}` flag, treated as part of the same gitflow-setup flag group as `--gitflow/--no-gitflow`. Default when omitted is `"pr"`. Re-running `kcia init --on-done=<value>` (idempotently) is also how the user changes the value later via CLI — no separate `kcia config` command is introduced.
4. **Backward compatibility**: `load_flow` silently defaults a missing `on_done` to `"pr"` — no prompt/backfill step, no migration required.
5. **Merge semantics** (`on_done == "merge"`), exact sequence:
   1. `git fetch origin`
   2. `git checkout <base>`
   3. `git pull origin <base>` (bring in the latest remote changes on the base branch)
   4. `git merge --no-ff <task-branch>` (merge the task/cycle branch directly into base)
   5. `git push origin <base>`
   6. Delete the task branch locally: `git branch -d <task-branch>`
   7. Delete the task branch on the remote: `git push origin --delete <task-branch>`
   8. End state: working tree stays checked out on `<base>`, up to date, ready for the next work cycle; task branch no longer exists locally or remotely.
6. **No override flags on `kcia done`**: `--push`/`--pr` are removed from `kcia done`. The command always executes the steps defined by the configured workflow (`on_done` for gitflow, push-by-default for current-branch) — no per-run override.
7. **Changing the choice later**: covered by decision 3 — re-run `kcia init --on-done=<value>`.
8. **Progress/step output**: `kcia done` must print the concrete steps it executes for the configured post-commit behavior (e.g. "Fetching base branch", "Merging task branch into base", "Pushing base branch", "Deleting task branch (local + remote)" for merge; "Opening PR to base" for PR; "Pushing commit" for current-branch), using the CLI's existing progress/animation output, so the user always sees what happened to their commit.
9. **Precondition failures**: `gh_available()` gates PR; a configured git remote gates push/merge-push. Any unmet precondition is a clear `typer.echo` error followed by `typer.Exit(1)` — no interactive fallback.
10. **Docs**: README.md (and control-plane docs if applicable) are updated to document `on_done`, the `--on-done` init flag, and the automatic step-by-step `kcia done` output.

## Steps to implement

1. **`cli/src/kcia/git/flow.py`**: add `on_done: Literal["pr", "merge"] = "pr"` to `GitFlow`. Update `load_flow` to default a missing/legacy `on_done` to `"pr"`. Update `save_flow` to persist it in `.ai/local/git.yaml`.
2. **`cli/src/kcia/commands/init.py`**:
   - Add `--on-done={pr,merge}` Typer option (default `None` = unset → falls back to `"pr"` unless interactively answered).
   - When gitflow is selected interactively and `--on-done` wasn't passed, prompt right after the gitflow selection (e.g. `typer.prompt("When kcia done finishes, merge directly to <base> or open a PR? [pr/merge]", default="pr")`), matching the existing prompt style (`init.py:248,265,273,331`).
   - Persist the resolved value via `save_flow`.
3. **`cli/src/kcia/commands/commit.py`**:
   - Remove the `--push`/`--pr` CLI options and the flag-gated block (~lines 234-249).
   - After `close_cycle`, branch on `flow.model`:
     - `CURRENT_BRANCH`: run the push step (existing push logic), printing a "Pushing commit to `<branch>`" step and the result.
     - `GITFLOW` with `on_done == "pr"`: run existing `_open_pr` logic automatically, printing "Opening PR to `<base>`" and the resulting PR URL.
     - `GITFLOW` with `on_done == "merge"`: run the new `_merge_to_base` implementing the 8-step sequence from Decision 5, printing each step (fetch, checkout base, pull, merge, push, delete local branch, delete remote branch) and a final confirmation.
   - Add precondition checks per Decision 9 before dispatch (`gh_available()` for PR; remote-configured check for push/merge), failing with `typer.echo` + `typer.Exit(1)`.
4. **`_merge_to_base`** (new function in `commit.py` or `git/repo.py`): implements the fetch/checkout/pull/merge/push/delete-local/delete-remote sequence via the existing `subprocess`-based git helpers in `git/repo.py`, returning per-step status for the progress output.
5. **Tests**:
   - `tests/test_gitflow_config.py`: round-trip of `on_done` in `save_flow`/`load_flow`; default-to-`"pr"` for legacy configs missing the field; `kcia init` interactive prompt and `--on-done` flag behavior.
   - `tests/test_git_cycle.py` (or a new test file): `kcia done` executing each configured behavior automatically (current-branch push, gitflow `on_done=pr`, gitflow `on_done=merge`) and printing the expected step output, using `CliRunner` with mocked subprocess/`gh` calls in the existing test style.
6. **Docs**: update README.md's `kcia init`/`kcia done` sections (and control-plane docs if they describe this workflow) per Decision 10.
7. **Version bump**: bump `VERSION` in `cli/src/kcia/__init__.py`. This is a **minor** bump — new config field (`on_done`) and new default automatic behavior for gitflow repos; removal of `--push`/`--pr` flags on `kcia done` is a breaking CLI surface change for any scripted callers using those flags, but the overall change is additive default behavior rather than removed functionality for end users, so minor is appropriate (final call to confirm at implementation time if `--push`/`--pr` removal is judged breaking enough to warrant major).

Implemented as **0.10.0 → 0.11.0** (minor). `--push`/`--pr` are gone from `kcia done`, but the same outcomes now happen automatically from config, so this stays a minor bump rather than a major.

## Note on uncommitted files

`tests/test_git_cycle.py`, `tests/test_gitflow_config.py`, and the untracked `.ai/` directory are currently present in the working tree but are **not** part of this plan and will not be touched/committed by it unless the plan is updated to explicitly include them.
