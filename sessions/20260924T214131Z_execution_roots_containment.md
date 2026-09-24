# Execution roots: accept roots nested inside the manifest roots

## Analysis

### Symptom

In `sport_monitor`, `kcia work approve` records the `implementation` approval and then aborts:

```
Approved `implementation`.
Execution roots for 'backend-dart' are not a subset of the manifest roots. Missing: ['docs/adr/**', 'packages/sport_monitor_models/test/fixtures/live_tracking/**']
```

The task can't move forward. Every retry fails the same way because `plan.md` doesn't change.

### Root cause

`waves/plan_execution.validate_execution_against_manifest` (`cli/src/kcia/waves/plan_execution.py:96`) checks
each execution root with exact string membership:

```python
missing = [r for r in exec_entry.roots if r not in manifest_entry.roots]
```

The `sport_monitor` manifest declares `backend-dart` roots as `['**', 'apps/sport_monitor_cloud_functions/functions/**', 'packages/sport_monitor_models/**']`.
The planner scoped the work *more narrowly* than that: `docs/adr/**` and
`packages/sport_monitor_models/test/fixtures/live_tracking/**`. Both paths sit inside `**`, and the second also sits
inside `packages/sport_monitor_models/**`, so the plan stays within the profile's territory. The exact-string check
still rejects it.

The planner prompt contributes to the problem. `control-plane/waves/prompts/analysis.md.j2:29-42` shows
`roots: ["<dir>/**"]` as a placeholder but never says whether roots must be copied verbatim from the manifest or may
narrow them. A planner that scopes tightly (which is desirable) gets punished.

Contributing factors:

1. **Late failure.** Nobody validates the execution block when the `analysis` wave writes `plan.md`. The validation
   first runs in `runner._run_multi_profile_wave` (`runner.py:995`), after the user has already approved
   `implementation`. The planner can no longer fix its own output, and the user is left with a stuck session.
2. **Single-profile block routed as multi-profile.** The prompt says to add the block "when multiple profiles change in
   parallel", but the planner emitted one for a single profile. `run_wave` (`runner.py:251-256`) sends *any* non-empty
   block through `_run_multi_profile_wave`. That path is functionally fine (and the narrowed roots usefully restrict
   `workspace_dirs`), so this isn't a bug in itself. It does mean single-profile plans hit the strict validator.
3. The error message states only a mismatch. It doesn't tell the user how to recover.

### Out of scope, noted

The `backend-dart` detection in `sport_monitor` produced a `'**'` root because a root-level `pubspec.yaml` exists. That
makes `backend-dart` overlap every other profile, so any future multi-profile plan that pairs `backend-dart` with
`mobile-flutter`/`web-flutter` using `**` would fail `validate_disjoint_roots`. That's a detection concern for a separate
plan.

## Open questions

1. Should validation also run at the end of the `analysis` wave (treat an invalid execution block like any other
   invalid wave output, so `analysis` fails and can be retried with `kcia work retry analysis`)? Recommended: yes.
2. Should a narrowed root that is a literal file path (no `/**`, e.g. `docs/adr/0007-live-tracking.md`) be accepted
   when it falls under a manifest root? Recommended: yes, with the same prefix containment. `_workspace_dirs_for_profile`
   already falls back to `repo_root` for non-`/**` shapes, so edit scope isn't widened beyond today's behavior.

### Answers

The user asked to implement without answering explicitly, so both recommended answers were adopted:

1. Yes. Validation runs at the end of `analysis`. When the block is invalid, the planner re-plans automatically with the
   error fed back as `## Previous validation error` (up to 2 extra attempts). Only if it is still invalid does the wave
   fail, before the `implementation` approval gate.
2. Yes. Literal paths nested under a `<dir>/**` manifest root are accepted.

## Proposed plan

### 1. Containment instead of equality — `cli/src/kcia/waves/plan_execution.py`

- Add `_root_covered(execution_root: str, manifest_roots: list[str]) -> bool`:
  - Exact string match → covered (preserves current behavior).
  - A manifest root of `**` or `.` → covers everything.
  - A manifest root `M/**` covers an execution root `P/**` or a literal path `P` when `P == M` or `P` starts with `M + "/"`.
  - Normalize by stripping a leading `./` and trailing `/`. Reject execution roots containing `..` segments or starting
    with `/` (never covered).
  - Anything else (complex globs) → not covered. Stay conservative, as today.
- `validate_execution_against_manifest` uses `_root_covered`. Update its docstring (it currently documents the "direct
  element" rule).
- The error message lists the profile's manifest roots and a recovery hint, e.g.
  `Fix the execution block in .ai/context/plan.md (roots must lie inside: [...]) and run kcia work again, or re-plan with kcia work retry analysis.`

### 2. Validate early — end of the `analysis` wave

- After `analysis` writes `plan.md`, parse the execution block. If one exists, run
  `validate_execution_against_manifest`, `validate_disjoint_roots` and `validate_execution_dependencies`. On
  `ExecutionBlockError`, mark the wave failed with that message, using the same mechanism the runner already applies to
  invalid wave outputs, so the user sees it before being asked to approve `implementation`.
- Keep the existing validation in `_run_multi_profile_wave` as a backstop (the user may edit `plan.md` by hand).
- Depends on the answer to open question 1.

### 3. Clarify the planner contract — `control-plane/waves/prompts/analysis.md.j2`

- State that each execution `roots` entry must be one of the profile's manifest roots, or a `<dir>/**` path nested
  inside one of them. Narrowing is encouraged, widening is rejected.
- Reiterate that the block is only for two or more profiles and should be omitted for a single-profile plan.

### 4. Tests — `tests/`

- `plan_execution` unit tests:
  - nested `<dir>/**` under `M/**` is accepted;
  - anything under `**` is accepted;
  - a sibling prefix (`packages/foo_bar/**` vs `packages/foo/**`) is rejected;
  - `..` and absolute roots are rejected;
  - an exact match is still accepted;
  - an unknown profile id still raises.
- A regression test reproducing the `sport_monitor` manifest and execution block from this report.
- An `analysis`-wave test: an invalid execution block fails the wave instead of surfacing after `approve` (if open
  question 1 is accepted).

### 5. Version

Bump `VERSION` in `cli/src/kcia/__init__.py` as a **patch** (0.23.0 → 0.23.1): it relaxes an over-strict validation and
moves an existing check earlier, with no new capability and no breaking change. If `control-plane` prompt edits require
it, bump `control-plane/VERSION` independently (patch).

## Implementation notes

- `plan_execution.py`: added `_root_covered` / `_normalize_root` and a `validate_plan_execution(plan_text, manifest)`
  helper that bundles the three checks. The new error message lists the manifest roots and how to recover.
- `runner.py`: added `_execution_block_error` for the `analysis` wave, plus a re-plan loop capped by
  `_EXECUTION_BLOCK_RETRIES = 2` inside `run_wave`. The multi-profile backstop is unchanged.
- `analysis.md.j2`: kept to two lines to protect the prompt budget. `tests/test_optimization_budget.py` rose
  17298 → 17337 (+39 tokens, documented inline; still under the 1.17× phase-0 cap of 17355).
- Tests: 11 new `plan_execution` cases (nested, sibling, `..`, absolute, globs, the sport_monitor regression, the error
  message, unknown profile) and 2 runner cases (self-healing re-plan; failure after retries before approval). Full
  suite: 458 passed.
- Verified the real `sport_monitor/.ai/context/plan.md` now validates.

## Version decision

- `cli/src/kcia/__init__.py`: 0.23.0 → **0.23.1 (patch)**. It relaxes an over-strict validation and moves an existing
  check earlier. No new capability, nothing breaking.
- `control-plane/VERSION`: 1.9.0 → **1.9.1 (patch)**. Prompt wording clarification only, matching precedent
  (ea4887b bumped 1.8.0 → 1.8.1 for a prompt-only change).

## Workaround for the stuck `sport_monitor` session (no kcia change needed)

Edit `sport_monitor/.ai/context/plan.md` and either:

- delete the second fenced `execution:` YAML block (single profile → normal single-profile path; edit scope becomes
  the whole repo, and the plan text still limits the work), or
- change its roots to `roots: ["**"]`.

Then run `kcia work` again. The `implementation` approval is already recorded.
