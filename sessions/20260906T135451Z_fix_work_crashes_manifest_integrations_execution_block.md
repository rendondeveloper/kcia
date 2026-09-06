# Fix unhandled crashes in `kcia work`: malformed manifest integrations and disjoint-root validation

## Analysis

Two separate unhandled crashes were hit while running `kcia work` end to end against the
`identaplusschool` repo. Scope of this plan, per final user direction: fix both crashes so the
flow does not blow up with a raw Python traceback. No new commands, no `kcia doctor` changes.

### Crash 1 — `classify_input()` assumes `integrations` is a dict

`cli/src/kcia/waves/session.py:78-79`:

```python
integrations = manifest.get("integrations") or {}
jira = integrations.get("jira") or {}
```

In `identaplusschool`'s `.ai/manifest.yaml`, `integrations` is a **list** of doc references
(`path`/`description` pairs), a different/older shape than the
`integrations: {jira: {enabled, project_keys}}` schema this code expects. `list.get` doesn't
exist, so this raises `AttributeError: 'list' object has no attribute 'get'` on every
`kcia work "<prompt>"` call for that repo, regardless of the prompt content.

**Fix**: guard the type before treating `integrations`/`jira` as dicts. If `integrations` (or
`integrations["jira"]`) is not a `dict`, treat it as `{}` (issue tracker not configured) instead
of raising — mirroring how a missing key is already handled (`or {}`). This is a one-line
robustness fix in `classify_input()`; no schema/behavior change for well-formed manifests.

### Crash 2 — `ExecutionBlockError` not caught in `work.py`

Call chain: `work.py:_execute` → `_run_loop` → `waves/runner.py:run_wave` →
`_run_multi_profile_wave` → `waves/plan_execution.py:validate_disjoint_roots`.

`validate_disjoint_roots()` raises `ExecutionBlockError` when it can't prove two profile
executions don't overlap (or, per its docstring, when a root isn't a simple `<dir>/**` /
whole-repo pattern it knows how to compare). `_run_loop`/`_execute` in `work.py` only catch
`ApprovalRequired`, `WaveBlocked`, and `WaveCancelled` around `run_wave(...)` — `ExecutionBlockError`
propagates unhandled, producing a raw traceback instead of the `typer.echo(...)` +
`raise typer.Exit(code=1)` pattern this repo's own conventions require for command failures
(CLAUDE.md "Conventions").

Inspecting the actual plan that triggered this (`identaplusschool/.ai/context/plan.md`) shows a
single-profile execution block (`roots: ["**"]`), and `validate_disjoint_roots` short-circuits
(`if len(executions) < 2: return`) for a single execution — so this exact plan, if re-run as-is,
would not currently reproduce the error; the crash occurred against a session/plan state with
more than one execution entry that is no longer the one on disk. Regardless, the underlying gap
is real and reproducible in general: **any** `ExecutionBlockError` raised during
`implementation`'s multi-profile validation currently surfaces as an unhandled traceback.

**Fix**: catch `ExecutionBlockError` in `work.py` at the same call sites as the existing
`ApprovalRequired`/`WaveBlocked`/`WaveCancelled` handling (`_execute`/`_run_loop`, lines ~451-543
and the `_run_loop` try block around line 526), and report it via `typer.echo(...)` +
`raise typer.Exit(code=1)`, consistent with the rest of the command's error handling. This does
not change `validate_disjoint_roots`'s validation logic or the set of root patterns it accepts —
it only ensures a validation failure is reported cleanly instead of crashing the process.

## Proposed plan

1. `cli/src/kcia/waves/session.py` — in `classify_input()`, guard `integrations` and
   `integrations["jira"]` to only be used as dicts when they actually are dicts; otherwise treat
   as `{}`.
2. `cli/src/kcia/commands/work.py` — catch `ExecutionBlockError` (import from
   `kcia.waves.plan_execution`) around the `run_wave(...)` calls in `_execute`/`_run_loop`,
   reporting via `typer.echo(...)` + `raise typer.Exit(code=1)`.
3. Add/extend tests: a `classify_input` test with `integrations` as a list (and as other
   non-dict types) asserting it falls back to `prompt` mode instead of raising; a `work` command
   test that triggers `ExecutionBlockError` during the implementation wave and asserts a clean
   `Exit(1)` with an echoed message instead of a raised exception.
4. Bump `cli/src/kcia/__init__.py:VERSION` — **patch** bump. Rationale: both changes are pure
   robustness/error-handling fixes with no new capability and no behavior change for
   well-formed manifests/plans; they only change what happens on previously-crashing inputs.

## Versioning

Patch bump, per point 4 above — bug fix only, no new capability, no breaking change.

**Applied:** `0.19.4` — guard non-dict manifest `integrations` in `classify_input()`; catch
`ExecutionBlockError` in `work.py` with clean `Exit(1)` instead of an unhandled traceback.
