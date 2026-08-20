# Multi-structure support + architect role for cross-profile orchestration

## Request (as given)

The CLI should support "multi structures" and gain an "architect" capability that
can manage several profiles at once, build a plan per profile, and run cross
tasks so agents work together across profiles.

## Current state (from reading the code)

- **Detection already supports multiple profiles per repo.** `profiles/detector.detect`
  walks candidate directories (including melos/pnpm/npm workspace roots) and can
  produce hits for several profiles at once; `.ai/manifest.yaml` stores one entry
  per detected profile with its `roots` glob list
  (`cli/src/kcia/profiles/detector.py`, `resolver.py`).
- **Resolution is path-scoped, not orchestration-aware.**
  `profiles/resolver.resolve_for_path` / `resolve_for_task` map a set of touched
  paths to the list of matching profile ids, ordered by manifest position. This
  answers "which profiles does this path touch," not "how should work be split
  and sequenced across profiles."
- **One task = one session, one linear pipeline.** `kcia work` creates a single
  `Session` (`.ai/local/session.json`) holding one `active_profiles` list and one
  `scope`. The five waves (`control-plane/waves/waves.yaml`) run **once**,
  sequentially, for that one session — there is no notion of running the
  pipeline per profile or fanning work out across profiles.
  `waves/runner.run_wave` / `run_waves_until` operate on exactly one `Session`.
- **Validation already is multi-profile.** `waves/validation.build_validation_plan`
  builds one validation step per active/touched profile and runs all of them —
  the one place today that already treats "several profiles in one task" as
  first-class.
- **Exactly two agent roles exist by design, and CLAUDE.md forbids a third.**
  `control-plane/agents/roles.yaml` declares `planner` and `builder` only, and
  the repo's CLAUDE.md states explicitly: *"Agent roles ... exactly two,
  `planner` and `builder`. Do not introduce a third."* An "architect" role, as
  named in the request, is a direct conflict with that constraint as currently
  written.
- **Waves are declarative but singular.** `control-plane/waves/waves.yaml` has
  five waves with a fixed `order` and `requires` graph; nothing today expresses
  "run wave X once per profile" or "wave X depends on wave Y completing across N
  profile-plans."

## Why this needs a plan, not a quick patch

This touches the domain model (`Session`, waves, manifest), the agent role
policy CLAUDE.md pins to exactly two roles, and the CLI's core interaction loop
(`kcia work`). It is architectural, not a local fix — per the repo's planning
workflow this must be scoped and confirmed before any code changes.

## Open questions

1. **"Architect" vs. the two-role rule.** ~~RESOLVED~~ — keep exactly two
   roles (`planner`, `builder`). No new agent identity. The `planner` gains the
   orchestration capability: given a task, it detects which profile(s) a
   monorepo's sub-projects (e.g. `backend`, `web`, `mobile`) are actually
   required for that specific task, and builds the plan accordingly — not
   necessarily touching every profile in the repo, only the one(s) the task
   needs.
2. **What does "multi structures" mean concretely?** ~~RESOLVED~~ — one
   unified plan (single `plan.md`) that references the profiles involved,
   not separate plan documents per profile.
3. **What does "cross tasks so agents work together" mean operationally?**
   ~~RESOLVED (direction), mechanism TBD below~~ — option (b): launch
   multiple builder agents **in parallel**, one per profile the task needs,
   each scoped to that profile's `roots`. This is the largest structural
   change in this request: `Session` currently models one task = one linear
   wave pipeline with a single repo-wide lock
   (`Session.acquire_lock`/`release_lock` in `waves/session.py`,
   `waves/runner.run_wave` operating on one `Session`). Parallel builders
   need, at minimum: N independent `edit_scope`-bounded implementation runs
   sharing the one approved `plan.md`, a way to launch/await them concurrently,
   per-profile status tracked separately (see Q6 below), and non-overlapping
   `edit_scope`s so two parallel builders can never write the same file
   (profiles' `roots` in the manifest should already be disjoint by
   construction, but this needs to be verified/enforced, not assumed).
4. **Manual profile targeting.** `kcia work` already accepts `--profile`
   (repeatable) and `--scope`. Should the new architect step *replace* manual
   `--profile` selection by auto-deriving the relevant profile set from the
   task text/ticket, or stay opt-in/advisory alongside the existing flags?
5. **"plan con sessions".** ~~RESOLVED~~ — one unified `plan.md` (Q2), and
   that plan document must itself declare the parallel execution: which
   profiles are involved and that each gets its own agent/session running
   concurrently. So `plan.md` gains an explicit "execution / profiles"
   section the `documentation-init` and `implementation` waves read to know
   how many per-profile sessions to spawn and for which profile ids.
6. **Failure semantics.** ~~RESOLVED~~ — (b) isolate. Other profiles' builders
   keep running to completion independently; the task ends in a mixed state
   (some profiles `completed`, one `failed`/`blocked`), and only the failed
   profile is retried/answered (scoped to that profile).

## Proposed plan

Summary of the confirmed shape: still exactly two agent roles. `analysis`
(planner) produces one unified `plan.md` that explicitly declares which
profile(s) the task needs and their scopes. Once that plan is approved,
`implementation` + `documentation-final` (builder) run **once per declared
profile, in parallel**, each bounded to that profile's root(s), sharing the
same approved plan as context. A profile that fails/blocks does not stop the
others (isolate semantics); it is retried/answered on its own.

### 1. `plan.md` gains a machine-readable profiles/execution section

- Extend the `analysis` prompt template
  (`control-plane/waves/prompts/analysis.md.j2`) so the planner is instructed
  to end `plan.md` with a fenced, parseable block, e.g.:
  ```yaml
  execution:
    profiles:
      - id: backend-dart
        roots: ["services/api"]
        summary: "add the /orders endpoint"
      - id: mobile-flutter
        roots: ["apps/mobile"]
        summary: "consume the new endpoint in the orders screen"
  ```
  The planner already has manifest/detection context available to it (it can
  reuse the existing `.ai/manifest.yaml` profile ids/roots — no new detection
  logic needed at the LLM level, just instructions to select from it and name
  which ones the task actually needs, per the "only the profile required for
  the task" direction).
- Add `cli/src/kcia/waves/plan_execution.py` (or similar) with a
  `parse_execution_block(plan_text: str) -> list[ProfileExecution]` parser +
  `ProfileExecution(profile_id, roots, summary)` dataclass. Validate against
  the loaded `Manifest` (unknown profile id → error surfaced like today's
  validation failures) and validate the declared `roots` are a subset of that
  profile's manifest `roots` (never wider) so a profile can't claim edit
  access outside what detection already scoped it to.
- If the block is missing or empty, fall back to the current behavior: a
  single implementation pass over `edit_scope: ["**"]` with no `--profile`
  narrowing (keeps today's single-project repos working unchanged).

### 2. Enforce disjoint `roots` before fan-out

- Before spawning parallel builders, verify the declared profiles' `roots`
  (as already recorded in `.ai/manifest.yaml`) do not overlap. Reuse
  `profiles/resolver._matches_any_root`-style prefix logic for the check.
  Overlap is a hard error (surfaced like today's manifest validation errors),
  since two parallel builders must never be able to write the same file.

### 3. Per-profile execution state replaces the single wave record for the parallelizable waves

- `waves/session.py`: extend `Session` so `implementation` and
  `documentation-final` status is tracked **per profile** once execution is
  multi-profile, e.g. `session.data["profile_runs"][profile_id]["waves"][wave_id]`,
  while `understanding`/`analysis`/`documentation-init` remain single top-level
  entries (they are planner-only, never parallel, per the resolved scope).
  Single-profile/legacy tasks keep using the existing flat `waves` dict
  unchanged — no schema migration needed for existing sessions.
- Bump `SESSION_SCHEMA_VERSION` and version the on-disk shape so `Session.load`
  can tell old sessions (flat waves) from new ones (flat + optional
  `profile_runs`).

### 4. Parallel builder execution in the runner

- `waves/runner.py`: add `run_wave_for_profile(wave_id, session, profile_id,
  edit_scope, ...)` that mirrors `run_wave` but scopes `RunRequest`
  (`workspace_dirs`/effective `edit_scope`) to the profile's `roots` and reads
  from `run_wave`'s existing prompt-building machinery with the profile's
  entry from the execution block folded in as extra context (so the builder
  for `mobile-flutter` only sees its own slice of the plan, not the whole
  thing verbatim — reuse `waves/prompts.py`'s budget/section-dropping
  machinery rather than reinventing it).
- Add `run_profiles_in_parallel(wave_id, session, executions, ...)` using a
  thread pool (matches the existing synchronous, blocking `run_provider`
  call shape — no need to introduce asyncio) that calls
  `run_wave_for_profile` concurrently, one thread per declared profile, and
  collects a `WaveResult` per profile.
- **Locking**: `Session.acquire_lock`/`release_lock` today is one repo-wide
  lock. Replace with a per-`(wave_id, profile_id)` lock (or a lock file per
  profile under `.ai/local/runs/<profile_id>.lock`) so parallel builders don't
  block each other, while same-profile re-entrancy is still prevented.
- **Isolation on failure (resolved Q6)**: `run_profiles_in_parallel` does not
  cancel sibling threads when one profile's `run_wave_for_profile` raises,
  blocks, or fails validation — it lets the others finish and returns a
  mixed list of `WaveResult`s (some `completed`, one `failed`/`blocked`).
  The overall wave is reported "completed" only once every profile's run is
  `completed`; a `failed`/`blocked` profile leaves the wave as
  partially-completed, surfaced explicitly (see CLI section below).

### 5. CLI surface (`commands/work.py`)

- `_run_loop`: when the active wave has multiple profile executions pending,
  call `run_profiles_in_parallel` instead of `run_wave`, and render a
  multi-line progress display (one status line per profile) instead of the
  current single `_ProgressReporter` line — extend `WaveProgress`
  (`waves/progress.py`) to support N concurrent named lines, or run N
  instances side by side.
- `work list` / `work show`: when `profile_runs` is present, show a
  status line per profile per wave instead of one line per wave.
- `work retry` / `work answer` / `work skip` / `work logs`: add an optional
  `--profile <id>` to target one profile's run when the task is multi-profile
  (required when more than one profile is `failed`/`blocked` simultaneously;
  optional/auto-selected when only one is).
- `work plan`: unchanged in spirit — still prints `plan.md`, which now
  includes the execution block, so the profile breakdown is visible to the
  human approving it before `implementation` starts.

### 6. Validation

- `waves/validation.build_validation_plan` already builds one step per
  profile; when running per-profile in parallel, scope each profile's
  validation call to just that profile's step(s) (it already keys by
  `profile_id`) instead of running the full multi-profile plan inside every
  thread.

### 7. Backward compatibility

- Single-profile (or no-profiles-detected) repos are unaffected: no execution
  block in `plan.md` → today's single `implementation` pass over the whole
  repo, unchanged code path, unchanged session shape.
- `--profile`/`--scope` manual flags on `kcia work` keep working as a manual
  override of what the planner would otherwise auto-derive.

### Explicitly out of scope for this change

- No new agent role/identity (confirmed — stays `planner`/`builder`).
- No cross-repo or cross-machine orchestration; parallelism is local threads
  within one `kcia work` invocation.
- No automatic dependency ordering between profiles (e.g. "backend before
  mobile") — all declared profiles run concurrently. If ordering turns out to
  be needed later, it is a separate follow-up plan.

### Files most likely touched

- `control-plane/waves/prompts/analysis.md.j2` (execution block instructions)
- `control-plane/waves/waves.yaml` (docs/description updates only, wave graph
  unchanged)
- `cli/src/kcia/waves/session.py` (`profile_runs`, per-profile locking,
  `SESSION_SCHEMA_VERSION` bump)
- `cli/src/kcia/waves/runner.py` (`run_wave_for_profile`,
  `run_profiles_in_parallel`)
- `cli/src/kcia/waves/plan_execution.py` (new — parser/validator for the
  execution block)
- `cli/src/kcia/waves/progress.py` (multi-line progress)
- `cli/src/kcia/commands/work.py` (`_run_loop`, `list`, `show`, `retry`,
  `answer`, `skip`, `logs` — `--profile` targeting)
- `tests/` — new coverage for the execution-block parser, disjoint-roots
  validation, per-profile session state, and isolate-on-failure semantics.

## Version bump

**Minor** (`0.x.0` → `0.(x+1).0` per current `VERSION`, semver judgment: new
capability, additive and backward compatible — no existing command,
single-profile session shape, or agent role is broken or removed). To be
applied to `cli/src/kcia/__init__.py:VERSION` after implementation, per the
mandatory step in CLAUDE.md's planning workflow.

Implemented now. Bumped `VERSION` to `0.7.0` (minor) and verified with `pytest`:
`248 passed`.
