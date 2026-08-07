"""Wave prompt composition."""

from __future__ import annotations

from pathlib import Path

import yaml

from kcia.paths import control_plane_root
from kcia.profiles.inheritance import resolve_inheritance
from kcia.profiles.loader import load_registry
from kcia.render import render_template
from kcia.waves.definitions import WaveDefinition, prompts_dir
from kcia.waves.session import Session, context_dir, load_manifest


def build_prompt(
    wave: WaveDefinition,
    session: Session,
    *,
    validation_error: str | None = None,
) -> str:
    repo_root = session.repo_root
    sections: list[str] = []

    roles_path = control_plane_root() / "agents" / "roles.yaml"
    roles_data = yaml.safe_load(roles_path.read_text(encoding="utf-8")) or {}
    role = next(
        (item for item in roles_data.get("roles", []) if item.get("agent") == wave.agent),
        None,
    )
    if role:
        sections.append(f"# Role: {wave.agent}\n")
        for output in role.get("expected_outputs", []):
            sections.append(f"- {output}")
        sections.append("")

    sections.extend(_guardrails_for_wave(wave.id))
    sections.append(_read_context_file(repo_root, "project.md"))

    registry = load_registry(repo_root)
    profile_ids = session.data.get("active_profiles") or []
    manifest = load_manifest(repo_root)
    if not profile_ids and manifest is not None:
        from kcia.profiles.resolver import resolve_for_cwd

        profile_ids = resolve_for_cwd(repo_root, manifest, repo_root)

    for profile_id in profile_ids:
        if profile_id not in registry.profiles:
            continue
        resolved = resolve_inheritance(profile_id, registry)
        sections.append(f"## Profile bundle: {profile_id}\n")
        for owner, reference in resolved.references:
            if reference.is_file():
                sections.append(reference.read_text(encoding="utf-8"))
                sections.append("")
        sections.append("### Rules\n")
        for key, value in resolved.rules.items():
            sections.append(f"- {key}: {value}")
        sections.append("")

    sections.append(_read_context_file(repo_root, "task.md"))
    if session.task.get("mode") == "ticket":
        sections.append(_read_context_file(repo_root, "ticket.md"))

    if wave.id in {"implementation", "documentation-final"}:
        sections.append(_read_context_file(repo_root, "plan.md"))

    if validation_error:
        sections.append(f"## Previous validation error\n\n{validation_error}\n")

    for injection in session.data.get("injections", []):
        sections.append(f"## Injected context\n\n{injection}\n")

    wave_instruction = render_template(
        prompts_dir(),
        wave.prompt_template,
        can_ask_questions=wave.can_ask_questions,
        validation_error=validation_error,
    )
    sections.append(wave_instruction)
    sections.append("\n## Output format\nRespond in Markdown.\n")

    return "\n".join(part for part in sections if part)


def _guardrails_for_wave(wave_id: str) -> list[str]:
    guardrails_dir = control_plane_root() / "guardrails"
    files = ["policies.yaml", "input-filter.md", "tool-control.md", "output-validation.md"]
    if wave_id == "implementation":
        files.append("reasoning-limits.md")
    chunks: list[str] = ["## Guardrails\n"]
    for name in files:
        path = guardrails_dir / name
        if path.is_file():
            chunks.append(path.read_text(encoding="utf-8"))
            chunks.append("")
    return chunks


def _read_context_file(repo_root: Path, name: str) -> str:
    path = context_dir(repo_root) / name
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")
