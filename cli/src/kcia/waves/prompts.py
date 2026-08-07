"""Wave prompt composition."""

from __future__ import annotations

from pathlib import Path

import yaml

from kcia.paths import control_plane_root
from kcia.profiles.inheritance import ReferenceEntry, ResolvedProfile, resolve_inheritance
from kcia.profiles.loader import load_registry
from kcia.render import render_template
from kcia.waves.budget import PromptStats, SectionStat, estimate_tokens
from kcia.waves.definitions import WaveDefinition, prompts_dir
from kcia.waves.session import Session, context_dir, load_manifest


def build_prompt(
    wave: WaveDefinition,
    session: Session,
    *,
    validation_error: str | None = None,
) -> str:
    prompt, _ = build_prompt_with_stats(wave, session, validation_error=validation_error)
    return prompt


def build_prompt_with_stats(
    wave: WaveDefinition,
    session: Session,
    *,
    validation_error: str | None = None,
) -> tuple[str, PromptStats]:
    repo_root = session.repo_root
    sections: list[str] = []
    stats = PromptStats()

    def add_section(name: str, content: str) -> None:
        stats.sections.append(
            SectionStat(name=name, chars=len(content), tokens=estimate_tokens(content))
        )
        if content:
            sections.append(content)

    roles_path = control_plane_root() / "agents" / "roles.yaml"
    roles_data = yaml.safe_load(roles_path.read_text(encoding="utf-8")) or {}
    role = next(
        (item for item in roles_data.get("roles", []) if item.get("agent") == wave.agent),
        None,
    )
    role_parts: list[str] = []
    if role:
        role_parts.append(f"# Role: {wave.agent}\n")
        for output in role.get("expected_outputs", []):
            role_parts.append(f"- {output}")
        role_parts.append("")
    add_section("role", "\n".join(role_parts))

    guardrails = "\n".join(_guardrails_for_wave(wave.id))
    add_section("guardrails", guardrails)

    project_context = _read_context_file(repo_root, "project.md")
    add_section("project-context", project_context)

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
        profile_parts: list[str] = [f"## Profile bundle: {profile_id}\n"]
        for entry in _references_for_wave(resolved, wave):
            if entry.path.is_file():
                profile_parts.append(entry.path.read_text(encoding="utf-8"))
                profile_parts.append("")
        profile_parts.append("### Rules\n")
        for key, value in resolved.rules.items():
            profile_parts.append(f"- {key}: {value}")
        profile_parts.append("")
        add_section(f"profile:{profile_id}", "\n".join(profile_parts))

    task_context = _read_context_file(repo_root, "task.md")
    add_section("task-context", task_context)

    ticket_context = ""
    if session.task.get("mode") == "ticket":
        ticket_context = _read_context_file(repo_root, "ticket.md")
    add_section("ticket-context", ticket_context)

    plan_context = ""
    if wave.id in {"implementation", "documentation-final"}:
        plan_context = _read_context_file(repo_root, "plan.md")
    add_section("plan-context", plan_context)

    validation_error_section = ""
    if validation_error:
        validation_error_section = f"## Previous validation error\n\n{validation_error}\n"
    add_section("validation-error", validation_error_section)

    injection_parts: list[str] = []
    for injection in session.data.get("injections", []):
        injection_parts.append(f"## Injected context\n\n{injection}\n")
    add_section("injections", "\n".join(injection_parts))

    wave_instruction = render_template(
        prompts_dir(),
        wave.prompt_template,
        can_ask_questions=wave.can_ask_questions,
        validation_error=validation_error,
    )
    add_section("wave-instruction", wave_instruction)

    output_format = "\n## Output format\nRespond in Markdown.\n"
    add_section("output-format", output_format)

    prompt = "\n".join(part for part in sections if part)
    return prompt, stats


def _references_for_wave(
    resolved: ResolvedProfile, wave: WaveDefinition
) -> list[ReferenceEntry]:
    """Filtra por tags. `wave.reference_tags is None` => todas."""
    if wave.reference_tags is None:
        return resolved.references
    if not wave.reference_tags:
        return []
    allowed = set(wave.reference_tags)
    return [entry for entry in resolved.references if allowed.intersection(entry.tags)]


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
