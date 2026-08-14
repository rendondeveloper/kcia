# Ignore generated `.ai/context/*`, `.ai/manifest.yaml`, `.ai/mcp.yaml`

## Analysis

`kcia start` (`cli/src/kcia/commands/init.py`) writes several files that are
fully machine-generated on every run:

- `.ai/manifest.yaml` — rewritten by `_write_manifest` every run (only skipped
  when content is unchanged; `detection.last_run` always changes upstream).
- `.ai/context/*.md` (`current.md`, `decisions.md`, `milestones.md`, `plan.md`,
  `project.md`, `task.md`) — `.ai/context/project.md` is written by
  `_write_context`, and the sibling files (`current.md`, `decisions.md`,
  `milestones.md`, `plan.md`, `task.md`) are runtime state produced by the
  wave pipeline (`kcia work`) as it runs, not source the user authors by hand.
- `.ai/mcp.yaml` — rendered by `kcia mcp add` (regenerable adapter config, same
  category as `.cursor/mcp.json`, which is already in `GITIGNORE_ENTRIES`).

None of these are currently covered by `.gitignore`'s `GITIGNORE_ENTRIES`
list (`cli/src/kcia/commands/init.py:38-49`), only `.ai/local/`, `.ai/cache/`,
`.ai/generated/`, `.ai/profiles/`, `CLAUDE.md`, `AGENTS.md`,
`.cursor/rules/`, `.cursor/mcp.json` are. As a result, running `kcia start`
(and later `kcia work`) leaves these files as untracked/staged changes in the
user's working tree, which is surprising and creates noisy diffs of
regenerated content.

Note: `_write_context`'s own comment says "The file is yours once it exists:
it is the one place to add what only a human knows" about
`.ai/context/project.md` specifically — implying that file was intended to be
human-edited and tracked. This plan treats `project.md` differently from the
other `context/*.md` files for that reason (see below).

## Open questions

None — scope confirmed by the user: ignore `.ai/context/*` (all of it),
`.ai/manifest.yaml`, and `.ai/mcp.yaml`.

This overrides the distinction the existing code comment draws for
`project.md` ("the file is yours once it exists"). The user's instruction is
explicit and covers the whole `context/` directory, so `project.md` is
ignored along with the rest rather than special-cased.

## Plan

1. In `cli/src/kcia/commands/init.py`, extend `GITIGNORE_ENTRIES` with:
   - `.ai/context/`
   - `.ai/manifest.yaml`
   - `.ai/mcp.yaml`

2. Update the module comment above `GIT_CONFIG_FILE` (lines 31-34) to keep it
   accurate: it currently only explains `.ai/local/`, `.ai/cache/`,
   `.ai/generated/`, and `.ai/history/`. Extend it to note that
   `.ai/context/`, `.ai/manifest.yaml`, and `.ai/mcp.yaml` are regenerable
   and excluded too.

3. `_write_context`'s inline comment ("The file is yours once it exists...")
   becomes misleading once `project.md` is gitignored by default — update or
   remove it to avoid contradicting the new gitignore behavior. The
   regenerate-only-if-missing / `--refresh-context` mechanics stay unchanged;
   only the comment's framing changes.

4. Since these entries are newly added to `GITIGNORE_ENTRIES`, any existing
   repo (like this one) that already ran `kcia start` will pick up the new
   `.gitignore` lines automatically the next time `kcia start` runs (the
   managed block is replaced wholesale — see `_update_gitignore`'s
   docstring). No migration code needed. This CLAUDE.md check-in itself will
   need `kcia start` re-run once implemented, or the `.gitignore` edited by
   hand, to actually ignore the currently-staged files in this repo — that is
   a follow-up action, not part of this code change.

5. No test currently pins `GITIGNORE_ENTRIES`'s contents (checked
   `tests/` for `GITIGNORE_ENTRIES`/`_update_gitignore` references before
   finalizing this plan) — a targeted grep as an implementation-time sanity
   check is enough; if such a test exists it must be updated to match the
   new entries.

## Version bump

Patch bump (`cli/src/kcia/__init__.py:VERSION`): this only changes which
generated files get gitignored by `kcia start`; no CLI surface, schema, or
behavior contract changes. Not a new capability, not breaking.

**Applied:** `0.4.1` → `0.4.2` (patch).
