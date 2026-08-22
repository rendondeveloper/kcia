# Handle "no test files" as a scaffolding signal instead of a hard validation failure

## Origin

User report: running validation against the `backend-dart` profile on a repo/package
that has no `test/` directory fails with:

```
Retry failed: validation failed:
- profile backend-dart (test): exit 65
No test files were passed and the default "test/" directory doesn't exist.
```

This is `dart test`'s own exit code (65) for "nothing to run" — not a bug in the
`kcia` command or in the profile's `test` command definition
(`dart test`, `control-plane/profiles/backend-dart/profile.yaml:28`). The profile
is correctly configured; the gap is in how `kcia` reacts when a profile's `test`
step fails specifically because there are no tests yet, versus a real failing
test run.

User's requested behavior: when this happens, `kcia` should not just hard-fail —
it should (1) inform that there are no tests, (2) continue with whatever else
needs to happen, and (3) if the check for tests happens during analysis and finds
none, it should create/add what's necessary to have tests in place.

## Current behavior (analysis)

- `cli/src/kcia/waves/validation.py`: `run_validation()` runs each profile's
required commands (`lint`, `test`, …) via `subprocess.run`, treats any nonzero
exit code as a `ValidationFailure`, and retries up to `retry_limit` times with
no distinction between failure causes.
- `cli/src/kcia/waves/runner.py:343-396`: for the `implementation` wave
(`wave.validation == "required"`), a validation failure re-invokes the builder
agent with the raw validation failure text appended to the prompt
(`validation_error=current_error`) and re-runs validation, up to
`retry_limit = 3` full agent+validate cycles. For any other wave, a validation
failure raises immediately (`raise RuntimeError(current_error)`).
- There is no dedicated "does this profile have tests" check anywhere in the
five waves (`control-plane/waves/waves.yaml`): understanding, analysis,
documentation-init, implementation, documentation-final. Detection
(`profiles/detector.py`) and validation only look at whether the required
*commands* exist on the resolved profile, never at whether a test suite
exists on disk.
- Nothing in `_LEAF_PREDICATES` / the detection DSL currently distinguishes
"exit 65, no test files" from any other nonzero exit; `ValidationFailure` only
stores `exit_code` and raw combined stdout+stderr.

So today: a profile with `test` configured but no `test/` directory yet will
burn through the implementation wave's full retry budget (3 agent invocations)
with a hard failure each time, and any other wave hits it as an immediate
`RuntimeError` — there's no "this profile just doesn't have tests yet, scaffold
them" path.

## Open questions

1. **Detection scope**: should "no tests exist yet" be detected generically
  (e.g. any nonzero exit whose combined output matches a small set of known
   "no tests found" signatures per language/tool: dart's exit 65 message, a
   possible pytest "no tests ran" exit 5, jest's "no tests found", etc.), or
   should it be scoped to `backend-dart`/`_dart-core` only for now, added to the
   profile schema (e.g. a new `validation.no_tests_signature` field per  profile) so each profile pack declares its own "empty test suite" signal? yes every profile need add this behaviour
2. **Which wave is responsible for scaffolding**: should the *analysis* wave be
  the one that checks "does this profile/root have a test directory" and, if
   missing, write that into `task.md`/the plan as required scope (so the
   *implementation* wave is the one that actually authors test files as part of
   normal wave output)? Or should this be handled purely inside the validation
   retry loop in `runner.py` (detect the "no tests" signature, and instead of
   passing the raw dart output as `validation_error`, pass an explicit  instruction like "no test suite exists yet for profile X at path Y — create  one before re-running validation")?Create the ave to reate the test ad 
3. **Validation semantics once scaffolding happens**: after the implementation
  wave creates tests, should validation still require `dart test` to exit 0
   (i.e. the newly created tests must actually pass), or is "a `test/` directory  with at least one test file now exists" itself sufficient to stop treating  the profile as failing? (Recommendation: still require exit 0 — an agent  that scaffolds a broken test is not done — but flagging this explicitly  since the user's phrasing ("continue with whatever's needed") could be read  either way.) - continue with whatever's needed"
4. Should this only apply inside the `implementation` wave's existing retry
  loop (which already re-invokes the builder agent on failure), or also change
   behavior for non-implementation waves, which today raise immediately on any
   validation failure? Non-implementation waves don't have `allow_edits` for
   arbitrary files per `waves.yaml`, so they may not be allowed to create test  files at all — worth confirming edit_scope per wave before deciding. yes oly on implementation wave 



## Decisions (from answers above)

1. This becomes a **profile schema capability**, not a hardcoded table: any
   profile can declare its own "empty test suite" signature. Populated now for
   the dart family (`_dart-core`, inherited by `backend-dart`,
   `mobile-flutter`, `web-flutter`) since that's the confirmed, reported case.
   Other profile packs can add their own signature later the same way.
2. No new wave. Handled inside the existing `implementation` wave's retry loop
   in `runner.py`: when a validation failure matches a profile's declared
   "no tests" signature, the retry prompt tells the builder agent explicitly
   to create the missing test suite, instead of forwarding dart's raw
   usage/help text.
3. Validation keeps requiring the `test` command to exit 0 on the next retry —
   "continue with whatever's needed" means the loop keeps going (agent creates
   tests, then validation re-runs and must pass), not that a missing/empty
   test dir is treated as a pass.
4. Scoped to the `implementation` wave only — other waves are not
   `allow_edits`-eligible for arbitrary file creation and keep today's
   immediate-failure behavior.

## Proposed plan

1. **Schema**: add an optional field to `ProfileSpec.validation`, e.g.:
   ```yaml
   validation:
     required_commands: [test, lint]
     optional_commands: [build]
     retry_limit: 3
     no_tests_signature:
       command: test
       exit_code: 65
       output_contains: ["No test files were passed"]
   ```
   Validated in `profiles/schema.py` (`schema_version` stays 2 — additive,
   optional field, no migration needed). `resolve_inheritance` already merges
   the `validation` dict shallowly per field, so `_dart-core` declaring it is
   enough for `backend-dart`/`mobile-flutter`/`web-flutter` to inherit it
   (leaf profiles can override if a specific tool ever needs a different
   signature).
2. **`_dart-core/profile.yaml`**: add `no_tests_signature` for `dart test`
   using the exact reported signature (`exit_code: 65`,
   `output_contains: ["No test files were passed"]`). Flutter's `flutter test`
   signature/exit code for an empty suite is not confirmed from this session's
   evidence — leave it unset for now rather than guessing; `mobile-flutter`/
   `web-flutter` fall back to no special handling (today's hard-failure
   behavior) until someone confirms flutter's actual signature and adds an
   override in those profiles.
3. **`waves/validation.py`**: `ValidationFailure` gains a way to check the
   match (e.g. a helper `matches_no_tests_signature(resolved_profile)` or a
   `no_tests: bool` computed at failure time using the resolved profile's
   `validation.no_tests_signature`). `run_validation`/`build_validation_plan`
   need the resolved profile's validation config available at failure time
   (today `ValidationStep` only carries `profile_id`, `cwd`, `command_name`,
   `command` — will need to also carry or look up the signature).
4. **`runner.py` implementation-wave retry loop (~line 355-394)**: when
   `report.failures` for a profile are all "no tests" matches, replace the
   forwarded text for that profile's entry in `current_error`/the retry prompt
   with an explicit instruction, e.g.: `"No test suite exists yet for profile
   <id> at <cwd> — create one covering the recent changes, then validation
   will re-run."` Failures that are real test failures (not the no-tests
   signature) keep forwarding the actual command output as today.
5. No change to `backend-dart` `commands.test` itself (`dart test` stays
   correct) — only the schema gains a new optional field and the
   implementation-wave retry path reacts differently to that specific,
   recognized failure signature.

## Version bump

Minor (`cli/src/kcia/__init__.py:VERSION`) — additive capability: new optional
`validation.no_tests_signature` profile schema field plus changed
implementation-wave retry messaging when it matches; no breaking change to
existing profiles or commands.

## Implementation

- **Version**: `0.13.0` (minor).
- Schema: optional `validation.no_tests_signature` (`command`, `exit_code`, `output_contains`). Inheritance uses `model_dump(exclude_none=True)` so children keep the parent signature.
- `_dart-core` declares dart test's empty-suite signal (`exit_code: 65`, `No test files were passed`). `backend-dart` inherits it. Flutter profiles inherit the same dict; they only match if `flutter test` actually emits that output.
- `ValidationStep` carries the signature; `matches_empty_suite` / `empty_suite_retry_message` rewrite the implementation-wave retry prompt. Real test failures still forward command output. Validation still requires exit 0 after scaffolding.