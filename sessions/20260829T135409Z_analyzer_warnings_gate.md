# Plan: mandatory analyzer gate (zero warnings/errors) for Dart/Flutter profiles

## Analysis

The user wants an additional validation gate: after any code change, the corresponding
Dart/Flutter analyzer must be run, and **any warning or error it reports must always be
fixed**, not just errors. This should apply to:

- `mobile-flutter` (Flutter mobile)
- `web-flutter` (Flutter web)
- backend, using the command that corresponds to it (pure Dart, no Flutter/fvm)

All three profiles already `extends: _dart-core`, and all three already define the
analyzer step:

- `mobile-flutter`/`web-flutter`: `commands.lint: "flutter analyze"`, with an
  `fvm`-aware `command_overrides` entry producing `"fvm flutter analyze"` when the repo
  pins a Flutter version (`.fvmrc` / `.fvm/fvm_config.json` present) — this is exactly
  the `fvm flutter analyze` gate the user asked for on mobile/web.
- `backend-dart`: `commands.lint: "dart analyze"` (with its own `fvm`-aware override to
  `"fvm dart analyze"`) — this is the command "that corresponds to the backend" the user
  asked for; it does not use `flutter`, since `backend-dart` is pure Dart, not Flutter.
- All three inherit `validation.required_commands: [test, lint]` from `_dart-core`
  (`web-flutter` only overrides to drop `optional_commands`, `required_commands` stays
  inherited), so `lint` (= analyze) is already a required command evaluated during the
  `implementation` wave (`control-plane/waves/waves.yaml`, `validation: required`).

So the command plumbing already exists per platform (`flutter analyze` /
`fvm flutter analyze` for mobile+web, `dart analyze` / `fvm dart analyze` for backend) —
nothing needs to be added there.

The actual gap is in the **instruction text**, in
`control-plane/profiles/_dart-core/references/validation.md` (shared by all three
profiles via inheritance), which currently says:

```
## Before handoff

- Run `verify` (test + lint) at the root of the affected package.
- Fix all analyzer errors before marking the task complete.
```

This only mandates fixing **errors**, not **warnings**. `dart analyze` / `flutter
analyze` exit non-zero on errors but can still print warnings/infos while exiting 0, so
an agent following this instruction literally could leave warnings unresolved and
consider the task complete. This is the gap the user is asking to close.

## Proposed plan

Edit `control-plane/profiles/_dart-core/references/validation.md` (single file, inherited
by `mobile-flutter`, `web-flutter`, and `backend-dart` — no changes needed to the
individual profile YAMLs or their `commands`, which already reference the correct
platform-specific analyzer):

- Strengthen the "Before handoff" bullet to make the analyzer run an explicit final gate
  that must be free of **both errors and warnings**, e.g.:

  ```
  ## Before handoff

  - Run `verify` (test + lint) at the root of the affected package as a final gate.
  - Fix every analyzer finding — errors and warnings alike — before marking the task
    complete. A task is not done while `analyze` output is non-empty.
  - If DCM is available in the project, run it on modified files.
  ```

- No changes to `control-plane/profiles/mobile-flutter/profile.yaml`,
  `control-plane/profiles/web-flutter/profile.yaml`,
  `control-plane/profiles/backend-dart/profile.yaml`, or
  `control-plane/profiles/_dart-core/profile.yaml`: their `commands.lint` /
  `commands.verify` and `command_overrides` (fvm-aware) already invoke the right
  analyzer per platform, and `validation.required_commands` already includes `lint`.
- No changes to `control-plane/waves/waves.yaml`: the `implementation` wave already has
  `validation: required` and pulls in the `validation` reference tag, which is how this
  strengthened `validation.md` reaches the builder agent during implementation.
- No CLI/schema changes: there is no dedicated "gate" field in `ProfileSpec`
  (`cli/src/kcia/profiles/schema.py`); this stays a documentation-level guardrail
  delivered through the existing `references` + `validation` reference-tag mechanism,
  consistent with how `_dart-core/references/validation.md` already works today.

### Version bump

Per CLAUDE.md, bump `cli/src/kcia/__init__.py:VERSION` after implementation. This is a
content/wording change to an existing control-plane reference doc (not a new profile
field, not a breaking schema change, not a new capability) — **patch** bump.

## Open questions

None — the existing per-platform commands already match what was requested
(`flutter analyze` / `fvm flutter analyze` for mobile-flutter and web-flutter, `dart
analyze` / `fvm dart analyze` for backend-dart); only the shared instruction wording
needs tightening to cover warnings, not just errors.

## Implementation notes

- Completed 2026-08-29.
- `validation.md` "Before handoff" now requires fixing errors **and** warnings; task
  is not done while `analyze` output is non-empty.
- `cli/src/kcia/__init__.py:VERSION` bumped **0.19.0 → 0.19.1** (patch: wording-only
  guardrail change in control-plane reference).
- No profile YAML, wave, or CLI schema changes.
- Prompt token cap in `test_optimization_budget.py` re-baselined to **16773** (+54).
