"""Run a cataloged skill through the builder provider."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

from kcia.config import resolve_agents
from kcia.providers.base import RunRequest
from kcia.providers.catalog import load_catalog as load_provider_catalog
from kcia.providers.registry import get_adapter
from kcia.providers.runner import call_provider, run_provider
from kcia.skills.constants import SKILL_OK_MARKER
from kcia.waves.blocked import detect_blocked
from kcia.waves.progress import WaveProgress
from kcia.waves.session import runs_dir


def detect_skill_ok(output: str) -> bool:
    if not output:
        return False
    return bool(re.search(r"SKILL_OK\s*:", output, re.IGNORECASE))


def build_skill_prompt(
    skill_file: Path,
    repo_root: Path,
    extra_argv: list[str],
) -> str:
    skill_text = skill_file.read_text(encoding="utf-8")
    extra = " ".join(extra_argv)
    extra_block = f"\n\nExtra arguments from the user: {extra}" if extra else ""
    return (
        "Execute the following skill file for this repository. "
        "Follow its instructions completely.\n\n"
        f"Skill file: {skill_file}\n"
        f"Repository root: {repo_root}\n"
        f"{extra_block}\n\n"
        "--- SKILL FILE ---\n"
        f"{skill_text}\n"
        "--- END SKILL FILE ---\n\n"
        "When finished, end your response with exactly one marker line:\n"
        "- `SKILL_OK: <short summary>` if you completed the skill successfully\n"
        "- `BLOCKED: <reason>` if you cannot proceed without more input\n"
        "Do not emit both markers."
    )


def _write_skill_run_files(
    repo_root: Path,
    run_id: str,
    prompt: str,
    output_text: str,
) -> tuple[Path, Path]:
    base = runs_dir(repo_root) / f"skill-{run_id}"
    base.parent.mkdir(parents=True, exist_ok=True)
    prompt_path = base.with_suffix(".prompt.md")
    output_path = base.with_suffix(".md")
    prompt_path.write_text(prompt, encoding="utf-8")
    output_path.write_text(output_text, encoding="utf-8")
    return prompt_path, output_path


def run_cataloged_skill(
    repo_root: Path,
    namespace: str,
    shortcut: str,
    skill_file: Path,
    extra_argv: list[str],
    *,
    provider_runner: Callable[..., object] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> tuple[int, str]:
    """Run the skill and return `(exit_code, stdout_text)`."""
    agents = resolve_agents(repo_root)
    builder = agents.get("builder")
    if builder is None:
        raise RuntimeError("builder agent is not configured")

    catalog = load_provider_catalog()
    if builder.provider not in catalog:
        raise RuntimeError(f"unknown provider '{builder.provider}'")

    adapter = get_adapter(builder.provider)
    if adapter.locate() is None:
        entry = catalog[builder.provider]
        raise RuntimeError(
            f"provider '{builder.provider}' is not installed. {entry.install_hint}"
        )

    prompt = build_skill_prompt(skill_file, repo_root, extra_argv)
    run_id = f"{namespace}-{shortcut}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"

    req = RunRequest(
        prompt=prompt,
        model=builder.model,
        mcp_config=None,
        mcp_tools=None,
        allow_edits=True,
        stream=adapter.capabilities.supports_streaming,
        workspace_dirs=[repo_root],
        session_id=None,
        resume=False,
        effort=builder.effort,
        allowed_tools=None,
        disallowed_tools=None,
        cwd=repo_root,
    )

    label = f"skill:{namespace}/{shortcut}"
    progress = WaveProgress(
        label,
        "builder",
        builder.provider,
        builder.model,
        periodic_updates=True,
    )
    runner = provider_runner or run_provider

    with progress:
        result = call_provider(
            runner,
            adapter,
            req,
            progress.handle,
            should_cancel,
        )

    output_text = getattr(result, "output_text", "") or ""
    exit_code = int(getattr(result, "exit_code", 0) or 0)
    _write_skill_run_files(repo_root, run_id, prompt, output_text)

    blocked = detect_blocked(output_text)
    if blocked:
        return 2, output_text

    if exit_code != 0:
        return 1, output_text

    if not detect_skill_ok(output_text):
        return 1, output_text

    return 0, output_text
