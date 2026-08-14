# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Editable install into the repo venv (already present at .venv/)
.venv/bin/pip install -e "./cli[dev]"

# Tests (pytest testpaths = ["tests"] is configured in cli/pyproject.toml,
# but pytest must be run from the repo root so tests/conftest.py puts cli/src on sys.path)
.venv/bin/pytest
.venv/bin/pytest tests/test_cli_help.py::test_help_lists_commands   # single test

# Run the CLI
.venv/bin/kcia --help
```

There is no linter or CI configured yet. `pipx install ./cli` is the documented end-user install.

## Architecture

kcia is a **control plane + CLI** that generates per-repository guidance for coding agents (Claude Code, Cursor). Two top-level halves:

- `cli/src/kcia/` — the Typer CLI (`kcia.main:app`). Most subcommands are still stubs that print `NOT IMPLEMENTED` and exit 1 (`commands/_stubs.py`); `profile` is the one substantially implemented command group. Adding a real command means replacing the stub `@app.callback(invoke_without_command=True)` in `commands/<name>.py`.
- `control-plane/` — versioned **data**, not code: profile packs, Jinja2 templates, agent roles, wave definitions, provider catalog, guardrail docs. Behavior changes here should be data edits, not Python edits. `paths.control_plane_root()` locates it as a sibling of `cli/` (`parents[3]` of the module file), so it only resolves for a source checkout.

### Domain model

- **Profile** — a technology package (`profile.yaml` + `references/` + `workflows/`) declaring detection rules, shell commands (`install`/`test`/`lint`/`verify`), coding rules, and adapter config. Profiles use single-parent inheritance via `extends`, capped at `MAX_INHERITANCE_DEPTH = 3`; abstract profiles (e.g. `_dart-core`) are inheritance-only and excluded from detection and `profile list`.
- **Pack** — a directory of profiles with a `pack.yaml` listing them and a `kcia_min_version` gate. Without `pack.yaml`, every subdirectory containing `profile.yaml` is loaded.
- **Agent roles** (`control-plane/agents/roles.yaml`) — exactly two, `planner` and `builder`. Do not introduce a third.
- **Waves** (`control-plane/waves/waves.yaml`) — five ordered steps (understanding → analysis → documentation-init → implementation → documentation-final), each bound to an agent with `allow_edits` / `edit_scope` / `writes` constraints. Waves are declarative; the CLI runner does not exist yet.

### Profile resolution pipeline

1. **Discovery** (`profiles/loader.discover_packs`) walks sources in fixed precedence order, later shadowing earlier: builtin (`control-plane/profiles`) → installed (`~/.local/share/kcia/packs`) → user (`~/.config/kcia/profiles`) → repo (`<repo>/.ai/profiles`) → `KCIA_PROFILE_PATH` env override. Shadowing is recorded in `ProfileRegistry.shadowed` rather than being an error.
2. **Loading** validates `ProfileSpec` (pydantic, `schema_version` pinned to 2; pack schema to 1), checks that every declared reference/workflow file exists, and validates detect predicates. `load_registry(..., strict=True)` raises; the default prints `warning:` and skips the bad pack — `kcia profile validate` is the strict path.
3. **Inheritance** (`profiles/inheritance.resolve_inheritance`) builds the chain root-first and merges shallowly per field (commands/rules/validation `update`, references/overrides append, adapters deep-merged), so the leaf wins.
4. **Detection** (`profiles/detector.detect`) picks candidate directories from workspace manifests when present (melos → pnpm-workspace → npm workspaces), otherwise walks up to depth 4 skipping `EXCLUDED_DIRS`, then evaluates each non-abstract profile's rules per directory.
5. **Manifest → active profiles** (`profiles/resolver`) — detection results are written to `.ai/manifest.yaml`; at query time a path is matched against each entry's `roots` (gitwildmatch via `pathspec`, with a fast path for `prefix/**`), falling back to `project.default_profile`. Results are ordered by manifest position, not match order.

### Detection predicate DSL (`profiles/predicates.py`)

A small closed language: combinators `all`/`any`/`not` plus a fixed `_LEAF_PREDICATES` set (`file_exists`, `yaml_present`, `json_any_key`, `file_contains`, …). Every predicate mapping must have **exactly one key**. Predicates are validated at pack-load time and evaluated with a per-run `_ReadCache` keyed on path+mtime; file reads are capped at `MAX_FILE_BYTES` and paths are confined to the candidate root via `_safe_path`. Adding a predicate means updating both `_LEAF_PREDICATES`/`_validate_leaf` and `_evaluate_leaf`.

### Generated repository state

`kcia init` is meant to produce `.ai/` in the target repo plus provider adapters rendered from `control-plane/templates/` (`render.render_template`, autoescape disabled — these are Markdown/YAML, not HTML). `.ai/local/`, `.ai/cache/`, and `.ai/generated/` are gitignored: treat anything under them as regenerable output, never hand-edited source.

## Planning workflow

All non-trivial change requests go through a plan file before any code is touched — this is the required workflow for this repo, not optional:

1. On a change request, first analyze it. If anything is ambiguous or underspecified, write the open questions down instead of guessing.
2. Create a plan file at `sessions/<timestamp>_<topic_slug>.md` (UTC timestamp, `topic_slug` in snake_case). If there are open questions, use a working `topic_slug` (e.g. `wip`) at creation time; once the plan is finalized, rename the file to a `topic_slug` that specifically describes what the plan is about.
3. The plan file contains: the analysis, the open questions (if any), and the proposed plan. Everything in the file is written in English, per the repo-wide English-only policy above.
4. The only output returned to the user after the initial prompt is the path/URL to the plan file, for them to review.
5. The user answers open questions or requests changes by editing that same plan file directly (adding answers under the questions, etc.) — do not create a second file for the same request.
6. Only start implementation once the plan in that file is confirmed.
7. After implementing the plan, bump `VERSION` in `cli/src/kcia/__init__.py`. This is mandatory on every change — no exceptions. Decide major/minor/patch by semver judgment based on what the plan changed (breaking change → major, new capability → minor, fix/internal-only → patch), and record which one was chosen, and why, in the plan file.
8. Commits made from this workflow must not carry a `Co-Authored-By` trailer for any AI tool (Claude, Cursor, or otherwise). No AI attribution/signature in commit messages.

## Conventions

- Everything is written in English now: control-plane YAML descriptions/hints, Python code, docstrings, and CLI help text. Match the surrounding file. This is a **permanent, non-negotiable policy** — do not revert to Spanish (or any other language) anywhere in this repo, including `.ai/history/sessions.jsonl` entries written via `kcia session log`. `commands/session.py` enforces this at the tool level (rejecting `¿¡ñÑ`) as a backstop for any agent, not just the one editing a given file.
- Commands report failures with `typer.echo(...)` + `raise typer.Exit(code=1)` rather than raising through. `errors.KciaError` exists but is not yet wired into a top-level handler.
- Version lives in `cli/src/kcia/__init__.py:VERSION` and is asserted by `tests/test_version.py`; `control-plane/VERSION` is a separate, independently versioned artifact.
