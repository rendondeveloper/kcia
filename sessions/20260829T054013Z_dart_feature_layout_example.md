# Plan: Add canonical Flutter/Dart feature layout example to `_dart-core`

## Analysis

### Request

Bring the architecture example the user expects from
`/Users/kikedev/Documents/Proyects/Aplazo/dart.aplazo-flutter-apps/ai-system/context/coding-standards/architecture-and-imports.md`
into this repo's Dart/Flutter profiles — specifically the canonical feature
layout (and the related barrel / import / file-position rules that make that
example meaningful).

This extends the prior plan
`sessions/20260829T052547Z_flutter_dart_layer_architecture_guardrails.md`
(already implemented). That pass added layer responsibilities and state
guardrails; it did **not** include the concrete feature tree / barrel topology
from the Aplazo coding-standards doc.

### Source material (what matters)

The referenced file is a full Aplazo workspace standard. The parts that
generalize into kcia `_dart-core` are:

1. **Barrel topology** — one barrel per layer (`data.dart`, `domain.dart`,
   `presentation.dart`), one feature barrel that re-exports only those three;
   no sub-folder barrels; absolute `package:` paths inside barrels; `show`
   required when crossing layer / feature / package boundaries.
2. **Master table of artifact positions** — where entity, repository
   interface, use case, data-source interface/impl, model, mapper, repository
   impl, cubit/bloc+state, screen, view live inside a feature.
3. **Five consolidated layout rules** — flat `domain/repositories/`; data-source
   interfaces live under `data/` (not domain); model→entity via a mapper class
   in `data/mappers/`; no sub-folder barrels; reference layout is the synthetic
   `feature_example`.
4. **Snippet — `feature_example` tree** — the concrete directory tree the user
   called out as the architecture example they expect to exist.
5. **Mapper class + repository-impl constructor injection** — short Dart
   snippets showing the mapper and how the repository impl receives datasource
   + mapper (DI-framework-agnostic; drop GetIt/`@lazySingleton` specifics).

### What will NOT be copied

- Aplazo package/workspace names (`aplazo`, `aplazo_tpv`, `aplazo_gallery`, …).
- Project-specific infra (`aplazo_connect`, Hive CE, `aplazo_local_storage`,
  SharedPreferences bans, Dio named clients).
- Manual GetIt `FeatureDI` modules, injectable annotations, injection markers.
- BLoC-vs-Cubit decision table and app-specific BLoC wiring (GoRouter,
  `bloc_providers.dart`) — prior plan kept state-management agnostic; Cubit
  paths in the tree are illustrative (cubit *or* bloc folder is fine).
- Spanish prose or ticket/plan leftovers — everything in English.

### Current state

- `_dart-core/references/architecture.md` already has layers, barrels (brief),
  dependencies, layer responsibilities, shared logic, state modeling.
- `_dart-core/references/coding.md` already requires `package:` + `show` across
  boundaries and mapper classes, but without the concrete layout tree.
- No `feature_example` tree exists in the control plane today.

### Proposed placement

Extend `_dart-core/references/architecture.md` (same file the prior plan
touched) rather than inventing a new reference file — keeps architecture +
layout as one source of truth for agents. Optionally tighten the existing
"Features and barrels" section so it does not contradict the stricter barrel
topology (today it only says "one folder per feature with a barrel" and
"each layer exposes its barrel"; it does not forbid sub-folder barrels or
require absolute `package:` exports).

## Open questions

1. **Scope of Dart code snippets in the reference:** include the short Mapper +
   RepositoryImpl examples from the source (token cost ~80–120 tokens), or only
   the directory tree + rules/tables (lighter)?
   - **Answer:** include both (plan recommendation) — confirmed by pointing at
     this plan to implement.
2. **Token budget:** the prior pass already raised understanding/total prompt
   tokens. Adding the full tree + import table will grow them again; tests
   (`test_prompt_composition.py`, `test_optimization_budget.py`,
   `understanding-baseline.md`) must be re-baselined deliberately.
   - **Answer:** OK to accept the bump — confirmed by pointing at this plan to
     implement.

## Proposed plan

1. **Rewrite/extend "Features and barrels"** in
   `_dart-core/references/architecture.md` with:
   - One barrel per layer; one feature barrel that re-exports only the three
     layer barrels; forbid sub-folder barrels.
   - Absolute `package:` paths required for all `export`/`import` inside
     barrels and for cross-file imports inside a feature (no `./` / `../`).
   - Import-direction table (same-layer / cross-layer / cross-feature /
     cross-package) with when `show` is mandatory.
   - Short checklist (bullet list, not markdown task checkboxes if that hurts
     rendering — prefer plain `-` bullets).

2. **Add "Feature layout (canonical)"** subsection with:
   - Master path table (generic `<feature>/…` paths, no Aplazo names).
   - The five consolidated rules (English, project-agnostic).
   - The `feature_example/` directory tree snippet as the reference layout.
   - Note that Cubit vs BLoC folder is illustrative; projects pick one state
     manager but keep the same layer positions.

3. **Add "Mapper at the data boundary"** short subsection with the mapper class
   + repository-impl constructor snippets, DI-agnostic wording ("register as a
   shared/singleton binding in the project's DI"), no GetIt code.

4. **`profile.yaml` rules:** add only if not redundant with existing keys:
   - Likely add `forbid_subfolder_barrels` and/or
     `require_absolute_package_paths_in_barrels` if they are not already
     covered by `require_layer_and_feature_barrels` /
     `require_package_imports_with_show_across_boundaries`.
   - Do **not** duplicate mapper/clean-architecture keys already present.

5. **Do not change** `flutter_ai_rules.md`, `mobile-flutter` / `web-flutter`
   references beyond inheriting `_dart-core`, or any CLI/Python code.

6. **Version:** bump `control-plane/VERSION` **1.7.0 → 1.8.0** (minor:
   additive architecture layout guidance). Leave CLI `VERSION` unchanged.

7. **Tests:** re-run `.venv/bin/pytest`; update prompt baseline fixture and
   token caps if the understanding/coding/architecture injection grows (same
   pattern as the prior plan's implementation notes).

## Out of scope

- Copying Aplazo DI modules, HTTP client rules, or BLoC decision matrices.
- New profile files or schema changes.
- Rewriting `coding.md` beyond a one-line cross-reference to the new layout
  section if needed to avoid contradiction.

## Implementation notes

- Completed 2026-08-29.
- Open questions resolved as recommendations (snippets included; token bump OK).
- `control-plane/VERSION` bumped **1.7.0 → 1.8.0** (minor).
- CLI `VERSION` unchanged.
- New `_dart-core` rule keys: `forbid_subfolder_barrels`,
  `require_absolute_package_paths_in_barrels`.
- `architecture.md` gained barrel topology, import-direction table, canonical
  artifact paths, `feature_example` tree, and mapper/repository-impl snippets.
- `coding.md` gained a one-line cross-ref to the architecture layout/mapper section.
- Prompt budgets re-baselined: understanding **2909** tokens; total task
  **16719** tokens.
