"""Staged multi-profile wave execution."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from kcia.providers.base import RunResult
from kcia.waves.runner import run_wave
from kcia.waves.session import Session, context_dir

ROOT = Path(__file__).resolve().parents[1]
KCIA = ROOT / ".venv" / "bin" / "kcia"
MELOS_FIXTURE = ROOT / "tests" / "fixtures" / "repos" / "melos_mono"


@pytest.fixture()
def multi_profile_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "melos_mono"
    shutil.copytree(MELOS_FIXTURE, repo)
    result = subprocess.run(
        [str(KCIA), "init", "--yes", "--path", str(repo)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return repo


def _write_plan(repo: Path, *, with_checklist: bool = False) -> None:
    checklist = ""
    if with_checklist:
        checklist = """
## Integration checklist

- backend-dart exposes `orderId` in the orders response
- mobile-flutter parses `orderId`
"""
    plan = f"""# Plan

Implement orders across backend and mobile.
{checklist}
```yaml
execution:
  profiles:
    - id: backend-dart
      roots: ["packages/api/**"]
      summary: "add orders endpoint"
    - id: mobile-flutter
      roots: ["packages/app_mobile/**"]
      summary: "consume orders endpoint"
      depends_on: [backend-dart]
    - id: web-flutter
      roots: ["packages/app_web/**"]
      summary: "unrelated web tweak"
```
"""
    context = context_dir(repo)
    context.mkdir(parents=True, exist_ok=True)
    (context / "plan.md").write_text(plan, encoding="utf-8")


def _ready_session(repo: Path) -> Session:
    session = Session.create(repo, text="implement orders", mode="prompt")
    for wave_id in ("understanding", "analysis", "documentation-init", "implementation"):
        session.set_wave_status(wave_id, "completed")
    session.save()
    return Session.load(repo)


def test_staged_execution_runs_backend_before_mobile(multi_profile_repo: Path) -> None:
    _write_plan(multi_profile_repo)
    session = _ready_session(multi_profile_repo)
    order: list[str] = []

    def runner(_adapter, req, **_kwargs):
        if "Profile bundle: backend-dart" in req.prompt:
            order.append("backend-dart")
        elif "Profile bundle: mobile-flutter" in req.prompt:
            order.append("mobile-flutter")
        elif "Profile bundle: web-flutter" in req.prompt:
            order.append("web-flutter")
        return RunResult(output_text="# done\n", exit_code=0)

    result = run_wave(
        "documentation-final",
        session,
        force=True,
        provider_runner=runner,
        skip_approval=True,
    )
    assert result.status == "completed"
    assert order.index("backend-dart") < order.index("mobile-flutter")
    assert "web-flutter" in order


def test_failed_dependency_skips_dependent_but_runs_unrelated(
    multi_profile_repo: Path,
) -> None:
    _write_plan(multi_profile_repo)
    session = _ready_session(multi_profile_repo)

    def runner(_adapter, req, **_kwargs):
        if "Profile bundle: backend-dart" in req.prompt:
            raise RuntimeError("backend failed")
        return RunResult(output_text="# done\n", exit_code=0)

    result = run_wave(
        "documentation-final",
        session,
        force=True,
        provider_runner=runner,
        skip_approval=True,
    )
    assert result.status == "failed"

    reloaded = Session.load(multi_profile_repo)
    mobile = reloaded.data["profile_runs"]["mobile-flutter"]["waves"]["documentation-final"]
    web = reloaded.data["profile_runs"]["web-flutter"]["waves"]["documentation-final"]
    assert mobile["status"] == "skipped"
    assert "backend-dart" in mobile["skip_reason"]
    assert web["status"] == "completed"


def test_integration_check_runs_after_merge(multi_profile_repo: Path) -> None:
    _write_plan(multi_profile_repo, with_checklist=True)
    session = _ready_session(multi_profile_repo)
    calls: list[str] = []

    def runner(_adapter, req, **_kwargs):
        if "integration check" in req.prompt.lower():
            calls.append("integration-check")
            return RunResult(
                output_text="## Integration check\n\n### orderId\n**Result:** PASS\n",
                exit_code=0,
            )
        calls.append("profile")
        return RunResult(output_text="# milestones\n", exit_code=0)

    result = run_wave(
        "documentation-final",
        session,
        force=True,
        provider_runner=runner,
        skip_approval=True,
    )
    assert result.status == "completed"
    assert calls[-1] == "integration-check"
    integration = context_dir(multi_profile_repo) / "integration-check.md"
    assert integration.is_file()
    assert "PASS" in integration.read_text(encoding="utf-8")
