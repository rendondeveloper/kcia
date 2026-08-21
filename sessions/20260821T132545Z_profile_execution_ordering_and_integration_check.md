# Dependency ordering + integration checklist for multi-profile execution

## Request (as given)

Follow-up to the already-shipped parallel multi-profile fan-out
(commit `00f856d`, plan `sessions/20260820T055909Z_parallel_multi_profile_orchestration.md`).
Reviewing a reference monorepo AI setup
(`/Users/kikedev/Documents/Proyects/Examples/sport_monitor/base-ia-nonorepo`)
surfaced two gaps worth closing, confirmed by the user:

1. Today all profiles declared in `plan.md`'s `execution:` block run fully in
   parallel, always — there is no way to say "backend before mobile/web"
   when a task adds a contract one profile must expose before another can
   consume it.
2. There is no check, after all profiles finish, that what one profile
   produced is actually consistent with what another profile expected (e.g. a
   response shape one side assumed).

Explicitly **not** in scope: no SDD-style explore/design/verify phases, no new
agent role, no fixed stack-specific checklist (Firestore/HTTP) — the
reference repo's items are project-specific; kcia stays generic.

## Current state (from reading the shipped code)

- `cli/src/kcia/waves/plan_execution.py`: `parse_execution_block` reads a
  fenced ` ```yaml ` block from `plan.md` under `execution.profiles`, each
  item is `{id, roots, summary}` → `ProfileExecution(profile_id, roots,
  summary)`. `validate_execution_against_manifest` checks ids/roots against
  `.ai/manifest.yaml`. `validate_disjoint_roots` rejects overlapping roots
  between any two declared profiles.
- `cli/src/kcia/waves/runner.py::_run_multi_profile_wave` (~line 771) is the
  fan-out entry point for `implementation`/`documentation-final`. It always
  submits **every** `ProfileExecution` to a `ThreadPoolExecutor` at once
  (`max_workers=len(executions)`, all futures submitted in the same loop) —
  there is no staging/waiting between profiles.
- `run_wave_for_profile` (~line 471) runs one profile's wave end to end
  (prompt build → provider call → validation retries → write outputs),
  taking a shared `save_lock` (a `threading.Lock`) so concurrent profile
  threads don't race on writing `session.json`.
- Failure handling already matches the isolate semantics from the prior plan:
  `_run_multi_profile_wave` collects all `WaveBlocked`/failed results after
  every future finishes, then reports mixed state — it does not cancel
  siblings.
- `documentation-final` already has a merge step
  (`_merge_documentation_final_milestones`) that concatenates each profile's
  `milestones-<profile_id>.md` into one `milestones.md` — this is the natural
  place to add an integration check, since it is the one point after every
  profile's implementation has already completed.
- Nothing today parses or acts on an inter-profile ordering/contract
  declaration — `ProfileExecution` has no such field, and `_run_multi_profile_wave`
  has no staging logic.

## Proposed plan

### 1. `depends_on` in the execution block

- Extend `ProfileExecution` (`plan_execution.py`) with `depends_on:
  list[str] = ()`, parsed from an optional `depends_on:` list per profile
  entry in the same fenced block, e.g.:
  ```yaml
  execution:
    profiles:
      - id: backend-dart
        roots: ["services/api/**"]
        summary: "add the /orders endpoint"
      - id: mobile-flutter
        roots: ["apps/mobile/**"]
        summary: "consume the new endpoint"
        depends_on: [backend-dart]
  ```
- Extend the `analysis` prompt template
  (`control-plane/waves/prompts/analysis.md.j2`) to tell the planner: default
  to no dependencies (full parallel, current behavior); only declare
  `depends_on` when one profile's work exposes something another profile's
  work consumes in the same task.
- Add `validate_execution_dependencies(executions)` in `plan_execution.py`:
  every id in `depends_on` must exist among the declared profiles (error
  otherwise, same `ExecutionBlockError` style as the existing validators),
  and the dependency graph must be acyclic (simple DFS cycle check — reuse
  the existing validation-error style, no new error type needed).

### 2. Staged execution in the runner

- `_run_multi_profile_wave`: replace the single "submit everything at once"
  loop with a small topological-wave scheduler: group `executions` into
  ordered batches by dependency depth (profiles with no unmet `depends_on`
  run in batch 0; a profile enters batch N once every id in its `depends_on`
  is in an earlier, **completed** batch). Submit one batch to the
  `ThreadPoolExecutor` at a time, waiting for the whole batch (`fut.result()`
  for every future in it) before submitting the next. Profiles with no
  `depends_on` at all keep running fully in parallel, exactly as today — this
  is additive, not a behavior change for the common case.
- Isolation semantics (already-shipped, from the prior plan) extend
  naturally: if a profile in batch N fails/blocks, its dependents in batch
  N+1 are never submitted (they can't meaningfully run against a failed
  dependency) but *unrelated* profiles already running in the same or later
  batches with no dependency on the failed one are unaffected. Record which
  dependents were skipped for this reason in their `profile_runs` entry
  (`status: "skipped"`, reason referencing the failed dependency) rather than
  leaving them `pending` with no explanation.

### 3. Integration checklist after fan-out completes

- Extend the `analysis` prompt template so, when the planner declares 2+
  profiles with a dependency edge (or, more generally, whenever a plan
  declares that one profile's change affects a contract another profile
  relies on — same signal as `depends_on`), it also writes an "Integration
  checklist" section in `plan.md` listing the concrete contract(s) to verify
  once every profile's implementation finished (e.g. "response field `X`
  returned by backend-dart matches the field mobile-flutter parses").
- Add this checklist to the `documentation-final` fan-out: after
  `_merge_documentation_final_milestones` succeeds, if `plan.md` has an
  "Integration checklist" section, run one more **single, non-parallel**
  builder pass (not per-profile — this reads across profiles, so it needs the
  whole repo) that checks the listed items against the actual diff and
  reports pass/fail per item into a new `.ai/context/integration-check.md`.
  Failure here does not roll back completed profile work; it's a report a
  human reviews, same posture as `work plan`'s human-approval gate elsewhere
  in the pipeline. If `plan.md` has no such section, this step is skipped
  entirely — no behavior change for tasks without cross-profile contracts.
- This is a new capability inside the existing `documentation-final` wave
  (still the `builder` role, still driven by `plan.md` content), not a new
  wave — keeps the wave graph in `waves.yaml` unchanged.

### 4. Backward compatibility

- No `depends_on` anywhere → same all-at-once parallel fan-out as today
  (batch 0 contains every profile).
- No "Integration checklist" section in `plan.md` → the extra
  `documentation-final` pass is skipped, output identical to today.
- Existing `ProfileExecution` construction sites (tests, `run_wave_for_profile`
  call sites) keep working since `depends_on` defaults to empty.

### Files most likely touched

- `cli/src/kcia/waves/plan_execution.py` (`depends_on` field, cycle/reference
  validation)
- `cli/src/kcia/waves/runner.py` (`_run_multi_profile_wave` batching,
  skipped-dependent bookkeeping, integration-check pass in
  `documentation-final`)
- `control-plane/waves/prompts/analysis.md.j2` (instructions for
  `depends_on` and the integration checklist section)
- `tests/test_plan_execution.py` (dependency parsing/validation, cycle
  detection)
- `tests/` for `runner.py` — new coverage for staged batches and
  skipped-dependent status
- possibly a new prompt template fragment for the integration-check pass if
  it needs distinct instructions from the rest of `documentation-final`

## Version bump

**Minor** — additive, backward compatible (no existing `plan.md` without
`depends_on`/checklist changes behavior). To be applied to
`cli/src/kcia/__init__.py:VERSION` after implementation.

**Applied:** `0.8.0` (minor — new `depends_on` staging and integration-check pass).
