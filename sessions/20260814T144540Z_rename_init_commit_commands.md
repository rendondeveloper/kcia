# Rename `init` -> `start` and `commit` -> `done`

## Analysis

The user wants two top-level CLI command renames, keeping identical behavior:

- `kcia init` -> `kcia start`
- `kcia commit` -> `kcia done`

Both commands are registered in `cli/src/kcia/main.py` via `app.command(...)`. The
underlying implementations live in `cli/src/kcia/commands/init.py` (function `init`)
and `cli/src/kcia/commands/commit.py` (function `commit_command`). No other code
references the command names as strings except the `ROOT_HELP` docstring in
`main.py` and any tests asserting on command names/help text.

## Open questions

None — scope and target names were confirmed directly with the user.

## Plan

1. In `cli/src/kcia/main.py`:
   - Change `app.command("init", ...)` to `app.command("start", ...)`, keep the
     same help text and underlying `init` function.
   - Change `app.command("commit", ...)` to `app.command("done", ...)`, keep the
     same help text and underlying `commit_command` function.
   - Update `ROOT_HELP` examples (`kcia init` -> `kcia start`, `kcia commit` ->
     `kcia done`).
2. Do not rename the underlying Python modules/functions (`commands/init.py`,
   `commands/commit.py`) — only the exposed CLI command name changes. Renaming
   the files is out of scope and adds churn with no behavioral benefit.
3. Search `tests/` and `cli/` for any other references to the string commands
   `"init"` / `"commit"` used as CLI invocations (e.g. `kcia init`, `kcia
   commit` in test assertions, echoed help text, or comments) and update them
   to `start` / `done`. This includes user-facing `typer.echo` strings in
   `commands/doctor.py`, `commands/wave.py`, `commands/profile.py`,
   `commands/branch.py`, `commands/init.py`, `git/flow.py`,
   `git/autobranch.py`, `profiles/bundle.py`, `waves/runner.py`. Explicitly
   NOT touched: `git init`/`git commit` (real git subcommands) and `kcia task
   init` (a different, unrelated command).
4. Run `.venv/bin/pytest` to confirm nothing else breaks.
5. Bump `VERSION` in `cli/src/kcia/__init__.py`.

## Versioning

This is a breaking change for any existing user/script invoking `kcia init` or
`kcia commit` (the old names stop working, no alias kept, per user's explicit
request for a straight rename). Per semver judgment: **minor** bump — pre-1.0
tool, CLI surface is still actively shaped, and no back-compat shim is wanted.
If the CLI is already >=1.0.0, this should instead be a **major** bump; will
check `VERSION` at implementation time and pick accordingly.
