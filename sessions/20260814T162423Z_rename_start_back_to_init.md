# Rename `kcia start` back to `kcia init`

## Analysis

Commit `c725295` renamed two CLI commands: `kcia init` → `kcia start` and `kcia commit` → `kcia done`, bumping `VERSION` to `0.2.0` as a breaking change. The user has confirmed that the `start` rename was a mistake and wants the initialization command to be named `kcia init` again. The `commit` → `done` rename is unaffected and stays as-is.

This is a partial revert of `c725295`: only lines that rename the `start`/init command are touched. Lines belonging to the `commit` → `done` rename, and the unrelated `kcia branch start` subcommand (a different command, under `commands/branch.py`, that predates and is unrelated to this rename), must not be touched.

Scope was established via a full-repo search (see command source, tests, docs, and docstrings below).

## Files and changes

### Command wiring
- `cli/src/kcia/main.py`
  - line 21: usage/help text `kcia start` → `kcia init`
  - line 34: `app.command("start", help="Initialize \`.ai/\` ...")(init)` → `app.command("init", help="Initialize \`.ai/\` ...")(init)`
  - line 25 (`kcia branch start`) — **leave unchanged**, unrelated subcommand.

### Command source (echo/comment strings only — function name `init` is already correct)
- `cli/src/kcia/commands/init.py:32` — comment referencing `` `kcia start` `` → `` `kcia init` ``
- `cli/src/kcia/commands/init.py:157` — echoed message `` `kcia start` `` → `` `kcia init` ``
- `cli/src/kcia/commands/profile.py:71` — echoed message
- `cli/src/kcia/commands/doctor.py:53` — echoed message
- `cli/src/kcia/commands/doctor.py:180` — echoed message
- `cli/src/kcia/git/autobranch.py:3` — module docstring
- `cli/src/kcia/git/flow.py:1` — module docstring
- `cli/src/kcia/waves/runner.py:293` — error message
- `cli/src/kcia/profiles/bundle.py:10` — generated-file header comment
- `cli/src/kcia/profiles/bundle.py:70` — docstring

### Tests
- `tests/test_cli_help.py:13` — `"start"` → `"init"` in expected command list
- `tests/conftest.py:26` — invocation arg `"start"` → `"init"`
- `tests/test_task_scope.py:21,51`
- `tests/test_tickets.py:32`
- `tests/test_repo_map.py:39`
- `tests/test_gitflow_config.py:155,162,169` (docstring + `["start", *args]` → `["init", *args]`; do NOT touch `test_git_flow.py`'s `["branch", "start", ...]`, unrelated)
- `tests/test_init.py:21,45,56,64,71,85,96,103`
- `tests/test_project_index.py:116,126,131`
- `tests/test_mcp.py:158`

### Documentation
- `README.md` — all `kcia start` occurrences (lines 7, 28, 38, 143, 145, 191, 195, 256, 259, 271, 275, 277, 511, 568, 679, 766, 791, 866, 880, 928, 940, 970, 973, 1207) → `kcia init`
- `CLAUDE.md:50` — `` `kcia start` `` → `` `kcia init` ``

### Explicitly out of scope (do not edit)
- `cli/src/kcia/commands/branch.py`, `tests/test_git_flow.py` — the unrelated `kcia branch start` subcommand
- `control-plane/guardrails/tool-control.md:9`, `tests/fixtures/prompts/understanding-baseline.md:173` — reference `branch start` / `done`, not this rename
- `CHANGELOG.md` — historical entries accurately describe past releases; not current-state docs
- Anything belonging to the `commit` → `done` rename

## Version bump

Per the mandatory versioning rule, bump `VERSION` in `cli/src/kcia/__init__.py`. This is a breaking rename of a public CLI command (undoing a prior breaking change), so it is a **major** bump under semver judgment: current `0.4.2` → `0.5.0` would be the minor convention used elsewhere in this repo's history for breaking CLI renames (see `c725295`, which went `0.1.x` → `0.2.0`, a minor bump despite being breaking — the project has not been using major version 1+ semantics yet). Following that established project precedent, this change bumps `0.4.2` → `0.5.0`.

## Open questions

None — scope confirmed with the user (revert `start` → `init` only, leave `done` as-is).
