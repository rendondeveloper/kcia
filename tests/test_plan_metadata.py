from __future__ import annotations

from pathlib import Path

from kcia.waves.plan_metadata import PlanMetadata, load, parse_plan_metadata

PLAN_WITH_METADATA = """# Plan

```yaml
type: fix
ticket: AUTH-123
title: Prevent duplicate device token registration
summary: >
  Skip the registration request when the token has not changed since the
  last successful call.
affected_files:
  modify: ["src/auth/device_token.py"]
  create: ["tests/auth/test_device_token_dedup.py"]
  delete: []
  tests: ["tests/auth/test_device_token_dedup.py"]
acceptance_criteria:
  - App start sends the request at most once per unchanged token.
```

## Description
Some free-form prose the analysis wave also writes.
"""


def test_parse_plan_metadata_extracts_all_fields() -> None:
    metadata = parse_plan_metadata(PLAN_WITH_METADATA)
    assert metadata is not None
    assert metadata.type == "fix"
    assert metadata.ticket == "AUTH-123"
    assert metadata.title == "Prevent duplicate device token registration"
    assert "Skip the registration request" in metadata.summary
    assert metadata.affected_files["modify"] == ["src/auth/device_token.py"]
    assert metadata.affected_files["create"] == ["tests/auth/test_device_token_dedup.py"]
    assert metadata.affected_files["delete"] == []
    assert metadata.acceptance_criteria == [
        "App start sends the request at most once per unchanged token."
    ]
    assert metadata.decisions == []


def test_parse_plan_metadata_returns_none_without_a_metadata_block() -> None:
    assert parse_plan_metadata("# Plan\n\nJust prose, no fence.\n") is None
    assert parse_plan_metadata("") is None


def test_parse_plan_metadata_ignores_the_execution_profiles_block() -> None:
    plan = """# Plan

Prose only, no title-bearing metadata block.

```yaml
execution:
  profiles:
    - id: backend-dart
      roots: ["services/api/**"]
```
"""
    assert parse_plan_metadata(plan) is None


def test_parse_plan_metadata_tolerates_malformed_yaml() -> None:
    plan = "# Plan\n\n```yaml\ntitle: [unterminated\n```\n"
    assert parse_plan_metadata(plan) is None


def test_load_returns_none_when_plan_file_is_missing(tmp_path: Path) -> None:
    assert load(tmp_path) is None


def test_load_returns_none_when_plan_has_no_metadata_block(tmp_path: Path) -> None:
    context = tmp_path / ".ai" / "context"
    context.mkdir(parents=True)
    (context / "plan.md").write_text("# Plan\n\nJust prose.\n", encoding="utf-8")
    assert load(tmp_path) is None


def test_load_merges_decisions_from_decisions_md(tmp_path: Path) -> None:
    context = tmp_path / ".ai" / "context"
    context.mkdir(parents=True)
    (context / "plan.md").write_text(PLAN_WITH_METADATA, encoding="utf-8")
    (context / "decisions.md").write_text(
        "# Decisions\n\n- Use a local cache file, not a database, to store the last token.\n"
        "- Keep the dedup check synchronous.\n",
        encoding="utf-8",
    )

    metadata = load(tmp_path)
    assert metadata is not None
    assert metadata.decisions == [
        "Use a local cache file, not a database, to store the last token.",
        "Keep the dedup check synchronous.",
    ]
    # Merging decisions must not lose the rest of the parsed metadata.
    assert metadata.title == "Prevent duplicate device token registration"


def test_load_without_decisions_file_leaves_decisions_empty(tmp_path: Path) -> None:
    context = tmp_path / ".ai" / "context"
    context.mkdir(parents=True)
    (context / "plan.md").write_text(PLAN_WITH_METADATA, encoding="utf-8")
    metadata = load(tmp_path)
    assert metadata is not None
    assert metadata.decisions == []


def test_plan_metadata_defaults_are_all_empty() -> None:
    metadata = PlanMetadata()
    assert metadata.type is None
    assert metadata.affected_files == {}
    assert metadata.acceptance_criteria == []
    assert metadata.decisions == []
