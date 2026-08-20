from __future__ import annotations

import pytest
import yaml

from kcia.profiles.schema import Manifest
from kcia.waves.plan_execution import (
    ExecutionBlockError,
    parse_execution_block,
    validate_disjoint_roots,
    validate_execution_against_manifest,
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

    with pytest.raises(ExecutionBlockError, match="subset"):
        validate_execution_against_manifest(
            [ProfileExecution("backend-dart", roots=["services/other/**"])],
            manifest,
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

