from __future__ import annotations

import pytest
import yaml

from kcia.profiles.schema import Manifest
from kcia.waves.plan_execution import (
    ExecutionBlockError,
    execution_batches,
    parse_execution_block,
    parse_integration_checklist,
    validate_disjoint_roots,
    validate_execution_against_manifest,
    validate_execution_dependencies,
)
from kcia.waves.plan_execution import ProfileExecution


def test_parse_execution_block_extracts_profiles() -> None:
    plan = """# Plan

Do it.

```yaml
execution:
  profiles:
    - id: backend-dart
      roots: ["services/api/**"]
      summary: "add the endpoint"
    - id: mobile-flutter
      roots: ["apps/mobile/**"]
      summary: "consume the endpoint"
```
"""

    executions = parse_execution_block(plan)
    assert [e.profile_id for e in executions] == ["backend-dart", "mobile-flutter"]
    assert executions[0].roots == ["services/api/**"]
    assert executions[0].summary == "add the endpoint"


def test_parse_execution_block_extracts_depends_on() -> None:
    plan = """# Plan

```yaml
execution:
  profiles:
    - id: backend-dart
      roots: ["services/api/**"]
      summary: "add the endpoint"
    - id: mobile-flutter
      roots: ["apps/mobile/**"]
      summary: "consume the endpoint"
      depends_on: [backend-dart]
```
"""
    executions = parse_execution_block(plan)
    assert executions[1].depends_on == ("backend-dart",)


def test_parse_integration_checklist_extracts_section() -> None:
    plan = """# Plan

## Integration checklist

- backend returns field `orderId`
- mobile parses `orderId`

## Risks

None.
"""
    assert parse_integration_checklist(plan) == "- backend returns field `orderId`\n- mobile parses `orderId`"


def test_parse_integration_checklist_returns_none_when_missing() -> None:
    assert parse_integration_checklist("# Plan\n\nNo checklist.") is None


def test_validate_execution_dependencies_rejects_unknown_dep() -> None:
    with pytest.raises(ExecutionBlockError, match="Unknown dependency"):
        validate_execution_dependencies(
            [
                ProfileExecution("a", roots=["x/**"], depends_on=("missing",)),
            ]
        )


def test_validate_execution_dependencies_rejects_cycles() -> None:
    with pytest.raises(ExecutionBlockError, match="Cyclic dependency"):
        validate_execution_dependencies(
            [
                ProfileExecution("a", roots=["x/**"], depends_on=("b",)),
                ProfileExecution("b", roots=["y/**"], depends_on=("a",)),
            ]
        )


def test_execution_batches_orders_by_dependency() -> None:
    a = ProfileExecution("a", roots=["x/**"])
    b = ProfileExecution("b", roots=["y/**"], depends_on=("a",))
    c = ProfileExecution("c", roots=["z/**"])
    batches = execution_batches([b, c, a])
    assert [sorted(e.profile_id for e in batch) for batch in batches] == [
        ["a", "c"],
        ["b"],
    ]


def test_parse_execution_block_returns_empty_on_missing_block() -> None:
    assert parse_execution_block("# Plan\n\nNothing here.") == []


def test_validate_execution_against_manifest_checks_roots_subset() -> None:
    manifest = Manifest.model_validate(
        {
            "schema_version": 2,
            "project": {"name": "x", "default_profile": "backend-dart"},
            "profiles": [
                {"id": "backend-dart", "roots": ["services/api/**"]},
            ],
            "dependencies": [],
        }
    )

    executions = [ProfileExecution("backend-dart", roots=["services/api/**"])]
    validate_execution_against_manifest(executions, manifest)

    with pytest.raises(ExecutionBlockError, match="not inside the manifest roots"):
        validate_execution_against_manifest(
            [ProfileExecution("backend-dart", roots=["services/other/**"])],
            manifest,
        )


def _manifest(roots: list[str]) -> Manifest:
    return Manifest.model_validate(
        {
            "schema_version": 2,
            "project": {"name": "x", "default_profile": "backend-dart"},
            "profiles": [{"id": "backend-dart", "roots": roots}],
            "dependencies": [],
        }
    )


@pytest.mark.parametrize("root", [
    "packages/models/**",
    "packages/models/test/fixtures/**",
    "packages/models/lib/src/model.dart",
    "./packages/models/test/**",
    "packages/models/",
])
def test_validate_execution_accepts_roots_nested_in_manifest_root(root: str) -> None:
    validate_execution_against_manifest(
        [ProfileExecution("backend-dart", roots=[root])],
        _manifest(["packages/models/**"]),
    )


@pytest.mark.parametrize("root", [
    "packages/models_extra/**",
    "packages/**",
    "packages/models/../secrets/**",
    "/etc/**",
    "packages/models/*.dart",
    "**",
])
def test_validate_execution_rejects_roots_outside_manifest_root(root: str) -> None:
    with pytest.raises(ExecutionBlockError, match="Outside"):
        validate_execution_against_manifest(
            [ProfileExecution("backend-dart", roots=[root])],
            _manifest(["packages/models/**"]),
        )


def test_validate_execution_accepts_anything_under_whole_repo_manifest_root() -> None:
    # Regression: sport_monitor's backend-dart owns `**`; the planner narrowed it.
    manifest = _manifest([
        "**",
        "apps/sport_monitor_cloud_functions/functions/**",
        "packages/sport_monitor_models/**",
    ])
    validate_execution_against_manifest(
        [ProfileExecution("backend-dart", roots=[
            "docs/adr/**",
            "packages/sport_monitor_models/test/fixtures/live_tracking/**",
        ])],
        manifest,
    )


def test_validate_execution_error_names_manifest_roots_and_recovery() -> None:
    with pytest.raises(ExecutionBlockError) as error:
        validate_execution_against_manifest(
            [ProfileExecution("backend-dart", roots=["docs/**"])],
            _manifest(["packages/models/**"]),
        )
    assert "['packages/models/**']" in str(error.value)
    assert "kcia work retry analysis" in str(error.value)


def test_validate_execution_still_rejects_unknown_profile() -> None:
    with pytest.raises(ExecutionBlockError, match="Unknown profile id"):
        validate_execution_against_manifest(
            [ProfileExecution("mobile-flutter", roots=["apps/**"])],
            _manifest(["**"]),
        )


def test_validate_disjoint_roots_allows_distinct_prefixes() -> None:
    validate_disjoint_roots(
        [
            ProfileExecution("a", roots=["packages/api/**"]),
            ProfileExecution("b", roots=["packages/mobile/**"]),
        ]
    )


def test_validate_disjoint_roots_rejects_overlapping_prefixes() -> None:
    with pytest.raises(ExecutionBlockError, match="Overlapping execution roots"):
        validate_disjoint_roots(
            [
                ProfileExecution("a", roots=["packages/**"]),
                ProfileExecution("b", roots=["packages/api/**"]),
            ]
        )


def test_validate_disjoint_roots_allows_single_profile_whole_repo_star() -> None:
    validate_disjoint_roots([ProfileExecution("mobile-flutter", roots=["**"])])


def test_validate_disjoint_roots_allows_single_profile_whole_repo_dot() -> None:
    validate_disjoint_roots([ProfileExecution("mobile-flutter", roots=["."])])


def test_validate_disjoint_roots_rejects_unprovable_root_with_multiple_profiles() -> None:
    with pytest.raises(ExecutionBlockError, match="Cannot validate disjoint roots"):
        validate_disjoint_roots(
            [
                ProfileExecution("a", roots=["**"]),
                ProfileExecution("b", roots=["packages/api/**"]),
            ]
        )

