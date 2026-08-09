"""Human-in-the-loop approval gate before code-changing waves."""

from __future__ import annotations

from pathlib import Path

import pytest

from kcia.providers.base import RunResult
from kcia.waves.definitions import get_wave, load_waves
from kcia.waves.runner import (
    ApprovalRequired,
    approval_document,
    require_approval,
    run_wave,
)
from kcia.waves.session import Session, context_dir


@pytest.fixture()
def git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    return repo


@pytest.fixture()
def planned(git_repo: Path) -> Session:
    Session.create(git_repo, text="add a loader", mode="prompt")
    session = Session.load(git_repo)
    context_dir(git_repo).mkdir(parents=True, exist_ok=True)
    (context_dir(git_repo) / "plan.md").write_text("# Plan\n\n1. Do it\n", encoding="utf-8")
    return session


def test_only_the_first_code_changing_wave_is_gated() -> None:
    gated = [wave.id for wave in load_waves() if wave.requires_approval]
    assert gated == ["implementation"]


def test_planning_waves_are_not_gated(planned: Session) -> None:
    for wave_id in ("understanding", "analysis", "documentation-init"):
        require_approval(planned, get_wave(wave_id))


def test_gate_raises_with_the_plan_attached(planned: Session) -> None:
    with pytest.raises(ApprovalRequired) as excinfo:
        require_approval(planned, get_wave("implementation"))
    assert excinfo.value.wave.id == "implementation"
    assert excinfo.value.document is not None
    assert "# Plan" in excinfo.value.document.read_text(encoding="utf-8")


def test_approval_unblocks_the_wave_and_persists(planned: Session) -> None:
    planned.approve("implementation", note="lgtm")
    require_approval(planned, get_wave("implementation"))

    reloaded = Session.load(planned.repo_root)
    assert reloaded.is_approved("implementation")
    assert reloaded.data["approvals"]["implementation"]["note"] == "lgtm"


def test_revoking_approval_re_arms_the_gate(planned: Session) -> None:
    planned.approve("implementation")
    planned.revoke_approval("implementation")
    with pytest.raises(ApprovalRequired):
        require_approval(planned, get_wave("implementation"))


def test_skip_approval_bypasses_the_gate(planned: Session) -> None:
    require_approval(planned, get_wave("implementation"), skip=True)


def test_run_wave_refuses_to_start_an_unapproved_wave(planned: Session) -> None:
    """The gate must fire before the provider is ever invoked."""
    calls: list[object] = []

    def spy(*_a, **_k):
        calls.append(1)
        return RunResult(output_text="done", exit_code=0)

    with pytest.raises(ApprovalRequired):
        run_wave("implementation", planned, force=True, provider_runner=spy)
    assert calls == [], "provider must not run before approval"


def test_gated_wave_stays_pending_so_the_run_can_resume(planned: Session) -> None:
    with pytest.raises(ApprovalRequired):
        run_wave("implementation", planned, force=True, provider_runner=lambda *a, **k: None)
    assert Session.load(planned.repo_root).wave_status("implementation") == "pending"


def test_approval_document_is_none_when_no_plan_was_written(git_repo: Path) -> None:
    Session.create(git_repo, text="add a loader", mode="prompt")
    session = Session.load(git_repo)
    assert approval_document(session, get_wave("implementation")) is None


def test_edits_to_the_plan_reach_the_builder_prompt(planned: Session) -> None:
    """The point of showing a path instead of a dump: editing it must matter.

    The prompt is composed when the wave runs, not when the plan was written,
    so a hand edit made during the pause is what the builder receives.
    """
    captured: dict[str, str] = {}

    def capture(adapter, req, **_kwargs):
        captured["prompt"] = req.prompt
        return RunResult(output_text="done", exit_code=0)

    plan = context_dir(planned.repo_root) / "plan.md"
    plan.write_text("# Plan\n\n1. Edited by hand\n", encoding="utf-8")
    planned.approve("implementation")

    try:
        run_wave("implementation", planned, force=True, provider_runner=capture)
    except RuntimeError:
        # Validation needs a real toolchain; the prompt was already composed.
        pass

    assert "Edited by hand" in captured["prompt"]
