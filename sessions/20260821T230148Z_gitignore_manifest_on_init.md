# Gitignore `.ai/manifest.yaml` when `kcia init` runs

## Request

`.ai/manifest.yaml` must appear in the project's `.gitignore` when the tool is initialized (`kcia init`).

## Analysis

`kcia init` already lists the file in `GITIGNORE_ENTRIES` (`cli/src/kcia/commands/init.py:46`) and `_update_gitignore` writes that tuple wholesale into the managed block (`# kcia — generated, do not commit`). Re-running init on an existing repo replaces the whole block, so no migration helper is needed.

The gap is that this contract is not pinned and the docs still describe the opposite policy:

- `tests/test_init.py::test_init_gitignores_everything_it_generates` only asserts a subset (`.ai/local/`, `.ai/cache/`, `.ai/generated/`, `CLAUDE.md`, `AGENTS.md`, `.cursor/rules/`). `.ai/manifest.yaml` is missing, so a regression would not fail CI.
- `README.md` still says the manifest is meant to be committed so the team shares detections:
  - around lines 282–286 ("Only `.ai/local/`, `.ai/cache/` and `.ai/generated/` are gitignored … `manifest.yaml`, `context/project.md` and `history/sessions.jsonl` are meant to be committed")
  - around lines 927–943 (gitignore example omits `.ai/manifest.yaml`, then states it is "deliberately **not** in that list")
  - line 853 ("Never delete `.ai/manifest.yaml` or `.ai/profiles/` — those are yours") — keep a "do not delete, just regenerate via `kcia init`" warning, but stop implying it is source you own in git.
- `CLAUDE.md` (Generated repository state) still names only `.ai/local/`, `.ai/cache/`, and `.ai/generated/` as gitignored. Update that sentence so it includes `.ai/manifest.yaml` (and, to stay accurate, the rest of `GITIGNORE_ENTRIES` that are also regenerable: `.ai/context/`, `.ai/mcp.yaml`).
- `cli/src/kcia/git/commit.py` `EXCLUDED_AI_SUBTREES` is unrelated: it is a commit-planner filter for regenerable subtrees, not gitignore. Leave it alone; gitignore is what keeps `manifest.yaml` out of `git status`.

This repo's own `.gitignore` is the kcia source tree, not a project produced by `kcia init`. It does not need `.ai/manifest.yaml` unless we later run `kcia init` here.

`--no-gitignore` remains the opt-out. No CLI flag, schema, or detection change.

## Open questions

None. The init-time ignore list already includes `.ai/manifest.yaml`; this plan locks that in tests and removes the docs that still tell people to commit it.

## Plan

1. In `tests/test_init.py::test_init_gitignores_everything_it_generates`, assert that `.ai/manifest.yaml` is present in the gitignore written by `kcia init`. Prefer asserting the full `GITIGNORE_ENTRIES` set is a subset of the written lines (import the tuple from `kcia.commands.init`) so the managed block cannot silently drop the manifest again. Keep the existing check that pre-existing entries such as `build/` are preserved.

2. Optionally add a one-liner in `test_init_creates_gitignore_when_absent` that `.ai/manifest.yaml` is in the newly created file. Not required if step 1 already imports `GITIGNORE_ENTRIES`.

3. Update `README.md` so it matches `GITIGNORE_ENTRIES`:
   - The "what start puts in your project" gitignore paragraph: `manifest.yaml` is regenerable output, not a committed team artifact. Keep `.ai/history/sessions.jsonl` as the file that is deliberately committed.
   - The "Where kcia lives" example block: include `.ai/manifest.yaml` (and the other entries already in `GITIGNORE_ENTRIES` that the example currently omits: `.ai/context/`, `.ai/mcp.yaml`, `.cursor/mcp.json`) so the snippet is the real managed block.
   - Drop the sentence that `.ai/manifest.yaml` is "deliberately **not** in that list".
   - Rephrase "Never delete `.ai/manifest.yaml`" so it is about regenerating via `kcia init`, not about treating the file as authored source.

4. Update the gitignore sentence in `CLAUDE.md` (Generated repository state) to include `.ai/manifest.yaml`.

5. Do not change `_write_manifest`, detection, or `--no-gitignore`.

## Version bump

Patch (`cli/src/kcia/__init__.py:VERSION`): `0.11.0` → `0.11.1`. The ignore entry is already in init; this change pins the contract and aligns docs. Not a new capability, not breaking.

**Applied:** `0.11.0` → `0.11.1` (patch).
