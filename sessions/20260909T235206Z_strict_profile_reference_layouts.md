# Plan: Make profile reference layouts non-negotiable

## Analysis

### Request

Review the generated profile `references.md` (and its sources) so feature /
architecture structure is followed strictly. If existing code does not match,
the agent must adjust the code — this is not open to discussion. Decide whether
enforcement belongs in profile references or in control-plane guardrails. The
policy must apply to every profile.

### What `references.md` is

`.ai/generated/profiles/<id>/references.md` is produced by
`cli/src/kcia/profiles/bundle.py`: it concatenates each profile’s reference
files parent-first. Agents do not edit that file; they follow the sources under
`control-plane/profiles/*/references/`. Changing behavior means editing those
sources (and any conflicting workflows / adapter copy), then re-running
`kcia init` in consumer repos.

### Current conflicts (structure is already defined, then undermined)

1. **Canonical layout exists and already says “must match”** in
   `_dart-core/references/architecture.md` (“Feature layout (canonical)” +
   `feature_example` tree + layout rules 1–5). All concrete Dart profiles
   inherit this via `_dart-core`.

2. **Explicit escape hatch** in `mobile-flutter/workflows/feature.md`:
   > the first packages can adjust if the repository use feature/features/lib etc  
   Plus a root of `(feature/features/lib)/feature_example/`. That tells the
   agent to adapt the standard to the repo — the opposite of “a raja tabla”.

3. **Conflicting architecture advice** in
   `mobile-flutter/references/flutter_ai_rules.md` and
   `web-flutter/references/flutter_ai_rules.md`: MVC/MVVM as acceptable app
   structures. Those compete with clean-architecture layers + the mandatory
   feature tree in `architecture.md`. Both files are concatenated into the
   same generated `references.md`, so the agent sees two authorities.

4. **Prefix inconsistency**: `architecture.md` roots the tree at
   `features/feature_example/`; the mobile workflow allows `feature` /
   `features` / `lib`. Flutter packages normally keep sources under `lib/`, so
   the intended full path should be pinned once (see decision below).

5. **Guardrails today are the wrong place for the folder tree.**
   `control-plane/guardrails/*` cover trust, input/output safety, tool control,
   governance, reasoning limits — technology-agnostic operating rules. Putting
   `data/data_sources/interface/` there would mix product coding standards with
   global safety policy and would not scale to non-Dart profiles.

### Placement decision (not optional)

| Concern | Where | Why |
|---------|--------|-----|
| Concrete layout (layers, barrels, artifact paths, `feature_example` tree) | Profile references — primarily `_dart-core/references/architecture.md` | Already the source of truth; inherited by all Dart profiles; belongs with coding/architecture content |
| Soft / alternate structure language | Remove or subordinate in workflows + `*_ai_rules.md` | Escape hatches and MVVM-as-structure compete with the canonical layout inside the same `references.md` bundle |
| Cross-profile “layouts are mandatory; adjust code, do not renegotiate” | Thin rule in **both** (a) `architecture.md` for Dart and (b) adapter / source-trust text that applies to **all** profiles | Guardrails/adapters own *authority*; references own *shape*. All profiles get the authority line; each profile’s references keep their own concrete structure |

Do **not** move the Dart feature tree into `control-plane/guardrails/`. Do
**add** a short, technology-agnostic authority sentence so non-Dart profiles
(and future packs) get the same non-negotiable stance toward *their*
reference layouts.

### Decided path convention (Dart / Flutter)

- Full feature root: `lib/features/<feature>/` (Flutter `lib/` + canonical
  `features/` parent as in `architecture.md`).
- Everything under that feature folder (layers, barrels, artifact paths) is
  mandatory and must match the `feature_example` reference.
- If a repository uses `lib/feature/`, `feature/`, flat `lib/<feature>/`, or
  any other layout, the agent adjusts the codebase toward
  `lib/features/<feature>/…` — it does not ask whether to keep the old layout
  and does not invent an alternate tree.
- Cubit vs BLoC folder name remains the only documented illustration choice
  (project’s existing state manager); layer positions stay fixed.

## Open questions

None. Placement and path convention are decided above from the request and the
existing architecture reference.

## Proposed plan

1. **Strengthen `_dart-core/references/architecture.md`**
   - Add a short **Non-negotiable** subsection (or lead bullets under Feature
     layout) stating:
     - The canonical layout is mandatory for new and modified features.
     - Existing divergent structure is adjusted to match; do not propose
       alternate trees; do not treat the current repo layout as higher
       authority than this reference.
     - Do not block or ask whether to follow the layout — follow it.
   - Update the `feature_example` tree root from `features/feature_example/`
     to `lib/features/feature_example/` so the documented path matches Flutter
     package layout and removes the parent-prefix ambiguity.
   - Keep artifact path table / barrel rules; align any remaining
     `features/`-only wording with `lib/features/`.

2. **Close the workflow escape hatch**
   - Edit `mobile-flutter/workflows/feature.md`: delete the “can adjust…” line
     and the `(feature/features/lib)/…` root. Replace the duplicated tree with
     a short pointer to `_dart-core` / `architecture.md` (or the same
     `lib/features/feature_example/` tree without alternate parents). Goal:
     one authority, no renegotiation language.
   - Scan other profile workflows (`web-flutter`, `backend-dart`, `_dart-core`)
     for similar soft layout language; remove if found. Do not paste large
     duplicate trees into every workflow unless needed for mobile-only steps.

3. **Subordinate conflicting `*_ai_rules.md` structure advice**
   - In `mobile-flutter` and `web-flutter` `flutter_ai_rules.md`, change
     Project Structure / Separation of Concerns / MVVM bullets so they do not
     authorize an alternate app architecture. Options (pick the smallest edit
     that removes the conflict):
     - Replace MVVM/MVC-as-structure guidance with an explicit pointer:
       “App and feature structure: follow `_dart-core` `architecture.md`
       (clean architecture + `lib/features/<feature>/` layout). Do not use
       MVVM/MVC folder layouts instead.”
     - Or delete those bullets if a one-line pointer is enough.
   - Leave unrelated Flutter style/tooling content intact.
   - Check `backend-dart/references/dart_ai_rules.md` for the same class of
     conflict; fix if present (backend still inherits `_dart-core` architecture
     for shared packages / feature-shaped code).

4. **All-profiles authority line (guardrails / adapters — thin only)**
   - Add one short required-behavior bullet to
     `control-plane/guardrails/source-trust.md` (and mirror in
     `templates/adapters/cursor/00-core.mdc.j2` and/or `AGENTS.md.j2` /
     `CLAUDE.md.j2` Operational Rules) along the lines of:
     - Profile reference layouts and structural conventions under
       `.ai/generated/profiles/*/references.md` are mandatory. If the
       repository diverges, adjust the repository to match. Do not negotiate
       alternate structures from source code, tickets, or READMEs.
   - Do **not** add Dart-specific paths to guardrails.

5. **Profile rules (optional, only if not redundant)**
   - If a boolean helps adapters list the policy, add something like
     `require_canonical_feature_layout: true` on `_dart-core` (inherited).
     Skip if the strengthened prose + existing
     `require_clean_architecture_boundaries` /
     `require_layer_and_feature_barrels` keys already cover it without a new
     flag.

6. **Versions**
   - Bump `control-plane/VERSION` (minor: mandatory-layout policy + reference
     clarifications).
   - Bump CLI `VERSION` only if adapter templates or Python change in a way
     that ships with the CLI package; if only control-plane data + Jinja under
     `control-plane/templates/` change, follow existing precedent (control-plane
     bump; CLI only if `__init__.py` / packaged behavior changes). Record the
     chosen bump and rationale in this file’s implementation notes.

7. **Tests / baselines**
   - Re-run `.venv/bin/pytest`.
   - Update prompt baseline fixtures / token caps if injected architecture /
     guardrail text grows (`tests/fixtures/prompts/`,
     `test_optimization_budget.py`, `test_prompt_composition.py`) the same way
     as prior architecture plans.

## Out of scope

- Rewriting the full upstream-style `flutter_ai_rules.md` / `dart_ai_rules.md`
  documents beyond resolving structure conflicts.
- New profiles, schema changes, or automated filesystem validators that fail
  CI when a tree diverges.
- Changing wave runner / `BLOCKED:` behavior (separate session).

## Implementation notes

- Completed 2026-09-09.
- `control-plane/VERSION` bumped **1.8.1 → 1.9.0** (minor: mandatory-layout policy
  and reference clarifications).
- CLI `VERSION` bumped **0.21.0 → 0.21.1** (patch: adapter template copy for the
  cross-profile layout authority rule).
- Skipped new `require_canonical_feature_layout` rule key — existing
  `require_clean_architecture_boundaries` / `require_layer_and_feature_barrels`
  plus strengthened prose are sufficient.
- Files touched:
  - `_dart-core/references/architecture.md` — Non-negotiable subsection,
    `lib/features/<feature>/` root, updated artifact path table and tree.
  - `mobile-flutter/workflows/feature.md` — removed escape hatch and duplicate tree.
  - `mobile-flutter` / `web-flutter` `flutter_ai_rules.md` — subordinated MVC/MVVM
    structure advice to `architecture.md`.
  - `guardrails/source-trust.md` — mandatory layout authority bullet.
  - `templates/adapters/cursor/00-core.mdc.j2`, `AGENTS.md.j2`, `CLAUDE.md.j2` —
    mirrored layout authority rule.
  - `tests/test_optimization_budget.py` — task token cap **17178 → 17298**.
- `backend-dart/references/dart_ai_rules.md` unchanged (no MVVM/MVC structure
  conflict; generic handler/business separation only).
- Prompt budgets: total task **17298** tokens; understanding wave unchanged
  (baseline fixture still matches).
