# Plan: Improve Flutter/Dart layer and architecture guardrails in profiles

## Analysis

### Request

Improve the `_dart-core`, `mobile-flutter`, and `web-flutter` profiles with clearer
expectations for the `data`, `domain`, and `presentation` layers, plus guardrails
(what not to do, what is allowed) and architecture rules for building Flutter/Dart
code well. The user pointed at
`/Users/kikedev/Documents/Proyects/Aplazo/dart.aplazo-flutter-apps/apps/aplazo_tpv/ai-system`
as the source of reference rules to take. Only architecture and code-authoring
guidelines should be pulled from that source — nothing else.

### Source material found

The referenced `ai-system` directory contains a single file:
`changes/MG-login-lockout/plan.md` — a concrete bug-fix/feature plan (login lockout
with a reusable countdown widget), written in Spanish, not a generic
architecture/guardrail document. There are no other files in that tree.

The user confirmed (via clarifying question) to proceed using this one plan as the
reference: extract the architecture patterns it demonstrates and fold them into the
existing profiles, rather than looking for a different source or dictating rules
directly.

### Current state of the profiles (already fairly developed)

- `_dart-core/references/architecture.md` — layering (presentation → domain →
  data), feature barrels, dependency direction (domain has no Flutter/infra deps).
- `_dart-core/references/coding.md` — imports, naming, complexity, models
  (`freezed` over `Equatable`, generated files untouched, mapper classes),
  control flow.
- `_dart-core/references/validation.md` — verify before handoff, no secrets, i18n
  for user-visible text.
- `_dart-core/references/testing.md`, `monorepo.md` — not yet reviewed line by
  line but exist.
- `_dart-core/profile.yaml` — `rules:` block encodes each guardrail as a named
  boolean (e.g. `require_clean_architecture_boundaries`,
  `require_mapper_classes_for_model_entity_mapping`).
- `mobile-flutter/references/flutter_ai_rules.md` and
  `web-flutter/references/flutter_ai_rules.md` — a large, generic (upstream-style)
  Flutter/Dart guide covering state management, routing, theming, layout,
  accessibility, etc. Broader in scope than layer/architecture guardrails.
- `mobile-flutter/profile.yaml` adds its own `rules:` (e.g.
  `keep_business_logic_out_of_widgets`, `require_accessibility_semantics`).

So the base is not empty — the task is to sharpen/extend it with the patterns
implicit in the referenced plan, not build layering guidance from scratch.

### Patterns extracted from the referenced plan (`MG-login-lockout/plan.md`)

1. **Presentation — rebuild/subscription correctness guardrail.** Bug 1 in the
   plan is a rebuild-filter predicate (a `BlocBuilder.buildWhen` in that specific
   codebase) that omits a state field that affects what's rendered, causing stale
   UI. This is generalized as a state-management-agnostic guardrail rather than a
   Bloc-specific one: whichever state management approach a project uses, every
   field read inside a widget's build/consume callback must be covered by
   whatever mechanism that approach uses to decide when to rebuild or notify
   listeners — omitting one is a defect, not a style nit. The rule is about using
   the chosen state manager correctly and consistently, not about prescribing
   which one to use. Concrete per-approach examples (Bloc/Cubit's
   `buildWhen`/`listenWhen`, Provider's `Selector`/scoped `context.watch`,
   Riverpod's `ref.watch(...select(...))`) are spelled out as plain-text bullets
   in the reference doc itself (see point 2 of the Proposed plan) rather than as
   code samples, to keep the reference token-light.
2. **Domain — repository contracts extended before data implements them.** New
   capability access (`saveLockoutExpiry`/`getLockoutExpiry`/`clearLockoutExpiry`)
   is added to the domain repository interface first; data supplies the
   implementation. Reinforces (and makes concrete) the existing "domain defines
   contracts, data implements them" rule with a data-source-interface step
   in between: domain repository interface → data repository impl → data source
   interface → data source impl. Presentation (regardless of the state manager
   used to hold its state) never talks to storage or an API client directly.
3. **Data — data source abstraction, one persistence key/format per concern.**
   Storage access sits behind a `*LocalDataSource` interface + impl, not inlined
   in the repository or the cubit. A single localStorage key constant represents
   one persisted concern (an expiry timestamp), read back and mapped to a rich
   domain type (`Duration`) at the boundary rather than passing raw primitives up.
4. **Reusable state/widgets belong in a shared layer, not the feature.** The
   plan places the generic countdown state holder and widget under
   `lib/core/commons/widgets/...`, explicitly decoupled from the `login` feature
   (guardrail: "the state holder of a reusable behavior must be generic, separate
   from the feature that first needed it, regardless of which state management
   approach it's built with" — R8 in the plan). This is a concrete
   instance of the existing `require_layer_and_feature_barrels` /
   "don't couple shared logic to one feature" intent, worth calling out
   explicitly with a positive example.
5. **State modeling — sealed/freezed unions for lifecycle-shaped state.** The
   countdown state is `idle | active(remaining) | finished`, a closed set of
   variants instead of loose booleans/nullable fields spread across a bigger
   state object. This complements the existing `require_freezed_over_equatable`
   rule with guidance on *when* to reach for a sealed union: to model a small,
   closed lifecycle, as opposed to a struct with `bool`/`nullable` flags (an
   anti-pattern that Bug 1/Bug 2 in the plan indirectly stem from: fields like
   `isLockedOut`/`lockoutRemainingTime`/`wrongPasswordAttemptsRemaining` grown ad
   hoc rather than through a small state union).
6. **Domain boundary-value correctness guardrail.** Bug 2 in the plan is an
   off-by-one error in a counting/threshold calculation living in the
   presentation-layer state holder (domain-adjacent business rule, regardless of
   which state management construct hosts it). Generalizable guardrail: any boundary/counter
   logic (attempts remaining, thresholds, pagination edges) must have its edge
   cases (0, max, max-1) covered by unit tests before being considered done —
   ties into the existing `require_tests_for_code_changes` rule but makes the
   "which cases" expectation explicit for this kind of logic.

### What will NOT be changed

- No code in `cli/` will be touched — this is a control-plane data change only.
- `flutter_ai_rules.md` (upstream-style broad guide) is left as-is; new content
  is layered guardrails/architecture, added to the existing
  `references/architecture.md`, `references/coding.md`, and possibly a new
  presentation-focused reference, not a rewrite of the broad guide.
- No new profile files, no schema changes, no new detection rules.
- Nothing from the plan's Spanish prose, project-specific naming
  (`TpvInstanceLocalKeys`, `AplazoButtonTpvVariant`), or the login-lockout feature
  itself is copied — only the generalizable architecture/pattern lessons.

## Open questions

None — the user has already confirmed the approach (use the single available plan
as reference material) via the clarifying question asked before this plan was
written.

## Proposed plan

1. **Extend `_dart-core/references/architecture.md`**
   - Add a "Layer responsibilities" subsection making explicit, per layer, what is
     allowed and what is forbidden:
     - Presentation: may hold ephemeral/UI state and call domain use
       cases/cubits; must not call data sources, storage, or HTTP clients
       directly; must not contain business rules (thresholds, persistence
       decisions).
     - Domain: owns business rules and repository *interfaces*; must not import
       Flutter, `dart:io`, or any data-source-specific package (already partly
       stated; make the "interfaces only, no implementation" split explicit).
     - Data: implements domain repository interfaces via one or more data
       sources (local/remote), each behind its own interface; maps
       storage/DTO primitives to domain types at the boundary; must not leak
       storage-specific types (raw JSON maps, SharedPreferences keys) past the
       data layer.
   - Add a "Shared/reusable logic" subsection: a cubit/widget/util used (or
     reusable) beyond one feature belongs in a core/shared location, not inside
     the feature that first introduced it; it must not import anything from a
     specific feature.
   - Add a short "State modeling" note: prefer a small closed `freezed` union
     for lifecycle-shaped state (idle/active/finished-style) over accreting
     boolean/nullable flags on one state class, with a one-line example
     contrasting the two shapes.

2. **Extend `_dart-core/references/coding.md`**
   - Add a "State manager consumption correctness" subsection (state-management
     agnostic — not anchored to Bloc). State the rule once, then give one
     plain-text example line per common approach so the guidance is concrete
     without embedding code blocks (keeps the reference token-light):
     - Rule: every field read inside a build/consume callback must be covered by
       that approach's own mechanism for deciding when to rebuild/notify.
       Missing one there is a bug, not a style preference.
     - Bloc/Cubit: list every state field the builder reads in `buildWhen`
       (or `listenWhen` for side effects); don't compare only a subset of
       fields.
     - Provider: scope `Consumer`/`context.watch` to the exact model/field
       needed (use `Selector` or a narrow `ChangeNotifier` getter) instead of
       watching the whole model and expecting every field change to redraw.
     - Riverpod: `ref.watch` every provider whose value the widget reads; if
       only one field of a bigger state should trigger a rebuild, watch a
       `.select((s) => s.field)` on that provider instead of the whole state
       object.
   - Add a short note under "Models"/domain logic: boundary and counter/threshold
     calculations must be accompanied by tests covering the edges (0, max,
     max-1), cross-referencing the testing guidance.

3. **Extend `_dart-core/references/testing.md`** (after reading its current
   content in the implementation pass) with the boundary-value testing
   expectation from point 2, if not already covered, to avoid duplicating
   guidance across two files.

4. **Add new rule keys to `_dart-core/profile.yaml` `rules:`** reflecting the
   above as boolean guardrails, following the existing naming convention, e.g.:
   - `require_state_manager_rebuild_scope_covers_rendered_fields` (state-manager
     agnostic — applies to `buildWhen`/`listenWhen`, watched values, read
     providers, etc., whichever the project uses)
   - `forbid_data_access_from_presentation`
   - `forbid_business_logic_in_data_or_presentation`
   - `require_shared_logic_outside_feature_scope`
   - `require_boundary_case_tests_for_counters_and_thresholds`
   (Exact final key names/count to be decided during implementation, keeping the
   `rules:` block internally consistent and non-redundant with existing keys
   such as `require_clean_architecture_boundaries`.)

5. **Review `mobile-flutter/profile.yaml` and `web-flutter/profile.yaml`** to
   check whether any of the new `_dart-core` rules should also be mirrored or
   specialized at the profile level (e.g. `mobile-flutter` already has
   `keep_business_logic_out_of_widgets`, which overlaps with point 4's
   presentation guardrail — reconcile instead of duplicating).

6. **No changes** to `flutter_ai_rules.md` in either `mobile-flutter` or
   `web-flutter`, to `_dart-core/references/monorepo.md`, or to any CLI/Python
   code.

7. **Version bump**: this is a control-plane-only content change (new guardrail
   guidance and new declarative rule keys in existing profiles, no schema or
   behavioral/CLI change). Per repo convention, `control-plane/VERSION` is
   versioned independently from `cli/src/kcia/__init__.py:VERSION`; only the
   former needs bumping, as a **minor** version (new guardrail capability
   surfaced to consuming agents, additive and backward compatible — no existing
   rule is removed or redefined).

8. **Testing/validation for this change**: run `.venv/bin/pytest` to confirm
   profile loading/validation still passes (new reference files must be listed
   in `profile.yaml`'s `references:`, and rule keys are free-form so no schema
   validation risk is expected, but should be checked with `kcia profile
   validate` for the touched profiles during implementation).

## Implementation notes

- Completed 2026-08-29.
- `control-plane/VERSION` bumped **1.6.0 → 1.7.0** (minor: additive guardrail
  rules and reference content; no schema or CLI behavior change).
- `cli/src/kcia/__init__.py:VERSION` unchanged — control-plane-only change.
- New `_dart-core` rule keys: `forbid_data_access_from_presentation`,
  `forbid_business_logic_in_data_or_presentation`,
  `require_shared_logic_outside_feature_scope`,
  `require_state_manager_rebuild_scope_covers_rendered_fields`,
  `require_boundary_case_tests_for_counters_and_thresholds`.
- `mobile-flutter` / `web-flutter`: no new rule keys added; existing
  `keep_business_logic_out_of_widgets` kept as a Flutter-specific presentation
  emphasis that complements inherited `_dart-core` rules.
- Prompt token budget tests and `understanding-baseline.md` fixture updated to
  account for the new reference content (+325 understanding tokens, +1615 total
  task tokens).
