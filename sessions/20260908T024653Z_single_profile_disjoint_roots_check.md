# Fix: `validate_disjoint_roots` fails on single-profile executions with a whole-repo root

## Analysis

Reported symptom (from a user running `kcia work` in a separate project, `identaplusschool`):

```
Cannot validate disjoint roots for 'mobile-flutter': root '**' is not a simple '<dir>/**' pattern.
```

Root cause is in `cli/src/kcia/waves/plan_execution.py`.

`validate_disjoint_roots` (line 139) is called from `waves/runner.py:939` during
`kcia work approve` (and effectively also on `kcia work`/`kcia work retry`, which
re-runs the same approval path). It iterates over **every** `ProfileExecution` in
the execution block and, for each of its roots, calls `_root_prefix()`:

```python
def _root_prefix(pattern: str) -> str | None:
    pattern = pattern.strip()
    if pattern in {".", "**"}:
        # Root spans whole repo: overlapping is impossible to prove disjoint.
        return None
    if pattern.endswith("/**"):
        return pattern[: -len("/**")]
    return None
```

When a root is `.` or `**` (a whole-repo root — exactly what `kcia init` produces
for a single-profile project, e.g. `mobile-flutter **` in the transcript above),
`_root_prefix` returns `None`. The caller then raises immediately:

```python
for root in exec_entry.roots:
    prefix = _root_prefix(root)
    if prefix is None:
        raise ExecutionBlockError(
            f"Cannot validate disjoint roots for {exec_entry.profile_id!r}: "
            f"root {root!r} is not a simple '<dir>/**' pattern."
        )
    prefixes.append(prefix)
roots_by_profile[exec_entry.profile_id] = prefixes
```

This happens **before** any pairwise comparison is attempted, and regardless of
how many profiles are in `executions`. The whole point of the function is to
detect *overlap between two or more profiles*; with only one profile in the
execution block, there is nothing to overlap with, so disjointness is trivially
satisfied. But the current code builds `roots_by_profile` unconditionally for
all entries first, so a single profile with an unprovable (whole-repo) root
still aborts the wave — even though no comparison will ever run.

This reproduces for any project where profile detection assigns a single
profile the root `**` or `.` (the common case for a single-module/single-package
repo), which then makes `kcia work approve` unusable for that project.

### Where the fix belongs

`validate_disjoint_roots` in `cli/src/kcia/waves/plan_execution.py`. The check
should only fail closed (raise "cannot validate") when the unprovable root
actually needs to be compared against another profile's root — i.e., when
`len(executions) > 1`. For a single-execution list, the function should return
without attempting to compute prefixes at all.

More precisely: keep collecting `roots_by_profile` as today when there are
2+ executions (existing behavior — including the "unprovable pattern" error —
is correct and desired there, since real overlaps must still be caught or
flagged as unprovable). When there is exactly 0 or 1 execution, skip the
prefix computation and pairwise loop entirely, since disjointness cannot be
violated.

### No open questions

The fix is a narrow, well-scoped bug fix confined to one function; behavior
for 2+ profile executions is unchanged. No ambiguity to resolve.

## Plan

1. In `cli/src/kcia/waves/plan_execution.py`, at the top of
   `validate_disjoint_roots`, add an early return when
   `len(executions) < 2`: nothing to compare, so nothing can overlap and no
   root pattern needs to be provable.
2. Add regression tests in `tests/test_plan_execution.py`:
   - A single execution with root `**` must pass `validate_disjoint_roots`
     without raising (covers the reported bug).
   - A single execution with root `.` must pass likewise.
   - Keep/verify the existing two-profile tests still pass unchanged
     (distinct prefixes allowed, overlapping prefixes rejected).
   - Add a two-profile case where one profile's root is `**` (unprovable) to
     confirm the existing "cannot validate" error still fires when there
     *are* multiple profiles to compare — this preserves the safety property
     the check exists for.
3. Run `.venv/bin/pytest tests/test_plan_execution.py` and the full suite
   `.venv/bin/pytest`.
4. Bump `VERSION` in `cli/src/kcia/__init__.py`. This is a bug fix with no
   API/behavior change for existing multi-profile cases → **patch** bump.

## Version bump

Patch bump (bug fix only, no new capability, no breaking change).

Implemented: `0.19.4` → `0.19.5` (patch).
