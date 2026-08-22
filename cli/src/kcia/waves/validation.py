"""Multi-profile validation planning and execution."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pathspec

from kcia.profiles.commands import resolve_commands
from kcia.profiles.inheritance import resolve_inheritance
from kcia.profiles.loader import load_registry
from kcia.profiles.resolver import resolve_for_task
from kcia.profiles.schema import Manifest
from kcia.waves.session import Session

COMMAND_ORDER = {"lint": 0, "test": 1, "verify": 2, "build": 3, "codegen": 4, "install": 5}


@dataclass(frozen=True)
class ValidationStep:
    profile_id: str
    cwd: Path
    command_name: str
    command: str
    empty_suite_signature: dict[str, Any] | None = field(default=None, compare=False)


@dataclass
class ValidationFailure:
    step: ValidationStep
    exit_code: int
    output: str


@dataclass
class ValidationReport:
    success: bool
    failures: list[ValidationFailure] = field(default_factory=list)
    passed_profiles: set[str] = field(default_factory=set)


def build_validation_plan(
    session: Session,
    manifest: Manifest,
    touched: list[Path],
    *,
    repo_root: Path,
) -> list[ValidationStep]:
    registry = load_registry(repo_root)
    profile_ids = session.data.get("active_profiles") or []
    if not profile_ids:
        profile_ids = resolve_for_task(touched, manifest, repo_root)

    for dep in manifest.dependencies:
        source = dep.get("source", "")
        triggers = dep.get("triggers_validation_of", [])
        spec = pathspec.PathSpec.from_lines("gitwildmatch", [source])
        for path in touched:
            rel = path.resolve().relative_to(repo_root).as_posix()
            if spec.match_file(rel):
                for profile_id in triggers:
                    if profile_id not in profile_ids:
                        profile_ids.append(profile_id)

    steps: list[ValidationStep] = []
    seen: set[tuple[str, str]] = set()

    for profile_id in profile_ids:
        if profile_id not in registry.profiles:
            continue
        loaded = registry.profiles[profile_id]
        resolved = resolve_inheritance(profile_id, registry)
        manifest_entry = next(
            (entry for entry in manifest.profiles if entry.id == profile_id),
            None,
        )
        roots = manifest_entry.roots if manifest_entry else ["."]
        cwd = _profile_cwd(repo_root, roots)
        commands = resolve_commands(resolved, manifest_entry, loaded.root, cwd)

        required = resolved.validation.get("required_commands", [])
        signature = resolved.validation.get("no_tests_signature")
        for command_name in required:
            command = commands.get(command_name)
            if not command:
                continue
            key = (str(cwd), command)
            if key in seen:
                continue
            seen.add(key)
            steps.append(
                ValidationStep(
                    profile_id=profile_id,
                    cwd=cwd,
                    command_name=command_name,
                    command=command,
                    empty_suite_signature=(
                        signature
                        if isinstance(signature, dict)
                        and signature.get("command") == command_name
                        else None
                    ),
                )
            )

    steps.sort(key=lambda step: (COMMAND_ORDER.get(step.command_name, 99), step.profile_id))
    return steps


def run_validation(
    plan: list[ValidationStep],
    *,
    retry_limit: int = 3,
    only_profiles: set[str] | None = None,
) -> ValidationReport:
    report = ValidationReport(success=True)
    scheduled = [
        step
        for step in plan
        if only_profiles is None or step.profile_id in only_profiles
    ]
    # Passing is tracked per step, not per profile: a profile whose `lint` passed
    # still has to run its `test`.
    passed_steps: set[ValidationStep] = set()
    pending = list(scheduled)

    for attempt in range(retry_limit):
        failures: list[ValidationFailure] = []
        for step in pending:
            if step in passed_steps:
                continue
            result = subprocess.run(
                step.command,
                shell=True,
                cwd=str(step.cwd),
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                passed_steps.add(step)
            else:
                failures.append(
                    ValidationFailure(
                        step=step,
                        exit_code=result.returncode,
                        output=(result.stdout + result.stderr).strip(),
                    )
                )
        report.passed_profiles = _fully_passed_profiles(scheduled, passed_steps)
        if not failures:
            report.success = True
            report.failures = []
            return report
        report.success = False
        report.failures = failures
        # Retry only what failed; steps that already passed are never re-run.
        pending = [failure.step for failure in failures]
        if attempt + 1 >= retry_limit:
            break

    return report


def matches_empty_suite(failure: ValidationFailure) -> bool:
    """Whether this failure is the profile's declared empty test-suite signal."""
    signature = failure.step.empty_suite_signature
    if not signature:
        return False
    if failure.exit_code != signature.get("exit_code"):
        return False
    output = failure.output or ""
    needles = signature.get("output_contains") or []
    return bool(needles) and all(needle in output for needle in needles)


def empty_suite_retry_message(failure: ValidationFailure) -> str:
    step = failure.step
    return (
        f"No test suite exists yet for profile {step.profile_id} at {step.cwd} "
        "— create one covering the recent changes, then validation will re-run."
    )


def _fully_passed_profiles(
    scheduled: list[ValidationStep], passed_steps: set[ValidationStep]
) -> set[str]:
    """Profiles whose every scheduled step passed."""
    by_profile: dict[str, list[ValidationStep]] = {}
    for step in scheduled:
        by_profile.setdefault(step.profile_id, []).append(step)
    return {
        profile_id
        for profile_id, steps in by_profile.items()
        if all(step in passed_steps for step in steps)
    }


def _profile_cwd(repo_root: Path, roots: list[str]) -> Path:
    if not roots:
        return repo_root
    root = roots[0]
    if root.endswith("/**"):
        root = root[:-3]
    if root in {".", "**"}:
        return repo_root
    return repo_root / root


