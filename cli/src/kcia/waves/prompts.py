"""Wave prompt composition."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from kcia.config import resolve_max_prompt_tokens
from kcia.paths import control_plane_root
from kcia.profiles.inheritance import ReferenceEntry, ResolvedProfile, resolve_inheritance
from kcia.profiles.loader import load_registry
from kcia.render import render_template
from kcia.waves.budget import PromptStats, SectionStat, apply_budget, estimate_tokens
from kcia.waves.definitions import WaveDefinition, load_budget_config, prompts_dir
from kcia.waves.repomap import build_repo_map
from kcia.waves.session import Session, context_dir, load_manifest

# Final prompt assembly order (stats/add_section call order stays unchanged for budget).
_SK_ROLE = 0
_SK_GUARDRAILS = 1
_SK_PROFILE_BASE = 2
_SK_PROJECT_CONTEXT = 3
_SK_TASK_STATEMENT = 10
_SK_RELATED_HISTORY = 11
_SK_REPO_MAP = 12
_SK_TASK_CONTEXT = 13
_SK_TICKET_CONTEXT = 14
_SK_PLAN_CONTEXT = 15
_SK_VALIDATION_ERROR = 16
_SK_INJECTIONS = 17
_SK_BLOCKED_PROTOCOL = 18
_SK_WAVE_INSTRUCTION = 19
_SK_OUTPUT_FORMAT = 20
_SK_CONTEXT_BUDGET = 21


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
    active_profile_ids_override: list[str] | None = None,
    plan_context_override: str | None = None,
) -> tuple[str, PromptStats]:
    repo_root = session.repo_root
    sections: list[tuple[float, str]] = []
    stats = PromptStats()

    def add_section(
        name: str,
        content: str,
        *,
        dropped: bool = False,
        sort_key: float,
    ) -> None:
        stats.sections.append(
            SectionStat(
                name=name,
                chars=len(content),
                tokens=estimate_tokens(content),
                dropped=dropped,
            )
        )
        if content and not dropped:
            sections.append((sort_key, content))

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
    role_content = "\n".join(role_parts)
    add_section("role", role_content, sort_key=_SK_ROLE)

    guardrails = "\n".join(_guardrails_for_wave(wave.id))
    add_section("guardrails", guardrails, sort_key=_SK_GUARDRAILS)

    # The request itself lives in the session, not on disk: in prompt mode nothing ever
    # writes .ai/context/task.md, so relying on that file alone runs every wave with no
    # problem statement at all.
    task_statement = _task_statement(session)
    add_section("task-statement", task_statement, sort_key=_SK_TASK_STATEMENT)

    history_content = ""
    if wave.include_history:
        from kcia.history.prompt_context import related_history_for_task

        task = session.task
        task_text = task.get("prompt") or task.get("title") or ""
        history_content = related_history_for_task(session.repo_root, task_text)
    add_section("related-history", history_content, sort_key=_SK_RELATED_HISTORY)

    project_context = _read_context_file(repo_root, "project.md")
    add_section("project-context", project_context, sort_key=_SK_PROJECT_CONTEXT)

    registry = load_registry(repo_root)
    manifest = load_manifest(repo_root)
    profile_ids = (
        active_profile_ids_override
        if active_profile_ids_override is not None
        else _active_profile_ids(session, manifest, repo_root)
    )

    repo_map = ""
    if manifest is not None:
        repo_map = build_repo_map(manifest, registry, repo_root)
    add_section("repo-map", repo_map, sort_key=_SK_REPO_MAP)

    profile_blocks: list[tuple[str, ResolvedProfile, list[ReferenceEntry]]] = []
    all_references: list[ReferenceEntry] = []
    rules_tokens = 0
    for profile_id in profile_ids:
        if profile_id not in registry.profiles:
            continue
        resolved = resolve_inheritance(profile_id, registry)
        filtered = _references_for_wave(resolved, wave)
        profile_blocks.append((profile_id, resolved, filtered))
        all_references.extend(filtered)
        rules_tokens += estimate_tokens(_rules_section(resolved))

    fixed_tokens = stats.total_tokens + rules_tokens
    budget = load_budget_config()
    kept_refs, dropped_refs = apply_budget(
        all_references,
        fixed_tokens=fixed_tokens,
        max_tokens=resolve_max_prompt_tokens(),
        drop_order=list(budget.drop_order),
    )
    kept_keys = {(entry.profile_id, entry.path) for entry in kept_refs}

    for profile_index, (profile_id, resolved, filtered) in enumerate(profile_blocks):
        profile_parts: list[str] = [f"## Profile bundle: {profile_id}\n"]
        for entry in filtered:
            if (entry.profile_id, entry.path) in kept_keys:
                if entry.path.is_file():
                    profile_parts.append(entry.path.read_text(encoding="utf-8"))
                    profile_parts.append("")
            else:
                content = ""
                if entry.path.is_file():
                    content = entry.path.read_text(encoding="utf-8")
                add_section(
                    f"profile:{profile_id}:{entry.path.name}",
                    content,
                    dropped=True,
                    sort_key=_SK_PROFILE_BASE + profile_index * 0.001,
                )
        profile_parts.append(_rules_section(resolved))
        add_section(
            f"profile:{profile_id}",
            "\n".join(profile_parts),
            sort_key=_SK_PROFILE_BASE + profile_index * 0.001,
        )

    task_context = _read_context_file(repo_root, "task.md")
    add_section("task-context", task_context, sort_key=_SK_TASK_CONTEXT)

    ticket_context = ""
    if session.task.get("mode") == "ticket":
        ticket_context = _read_context_file(repo_root, "ticket.md")
    add_section("ticket-context", ticket_context, sort_key=_SK_TICKET_CONTEXT)

    if plan_context_override is not None:
        plan_context = plan_context_override
    else:
        plan_context = ""
        if wave.id in {"implementation", "documentation-final"}:
            plan_context = _read_context_file(repo_root, "plan.md")
    add_section("plan-context", plan_context, sort_key=_SK_PLAN_CONTEXT)

    validation_error_section = ""
    if validation_error:
        validation_error_section = f"## Previous validation error\n\n{validation_error}\n"
    add_section("validation-error", validation_error_section, sort_key=_SK_VALIDATION_ERROR)

    injection_parts: list[str] = []
    for injection in session.data.get("injections", []):
        injection_parts.append(f"## Injected context\n\n{injection}\n")
    add_section("injections", "\n".join(injection_parts), sort_key=_SK_INJECTIONS)

    wave_instruction = render_template(
        prompts_dir(),
        wave.prompt_template,
        can_ask_questions=wave.can_ask_questions,
        validation_error=validation_error,
    )
    add_section(
        "blocked-protocol",
        render_template(
            prompts_dir(),
            "_blocked.md.j2",
            can_ask_questions=wave.can_ask_questions,
        ),
        sort_key=_SK_BLOCKED_PROTOCOL,
    )
    add_section("wave-instruction", wave_instruction, sort_key=_SK_WAVE_INSTRUCTION)

    output_format = "\n## Output format\nRespond in Markdown.\n"
    add_section("output-format", output_format, sort_key=_SK_OUTPUT_FORMAT)

    if dropped_refs:
        names = ", ".join(entry.path.name for entry in dropped_refs)
        budget_block = (
            "## Context budget\n\n"
            f"The following guidance was omitted to fit the context budget: {names}.\n"
            "Ask for it explicitly if you need it.\n"
        )
        add_section("context-budget", budget_block, sort_key=_SK_CONTEXT_BUDGET)
        wave_state = session.waves.setdefault(wave.id, {"status": "pending", "attempts": 0})
        wave_state["dropped_references"] = [entry.path.name for entry in dropped_refs]
        session.save()

    prompt = "\n".join(content for _, content in sorted(sections, key=lambda item: item[0]))
    return prompt, stats


def _active_profile_ids(
    session: Session, manifest, repo_root: Path
) -> list[str]:
    profile_ids = session.data.get("active_profiles") or []
    if profile_ids:
        return profile_ids
    if manifest is None:
        return []
    scope = session.task.get("scope") or []
    if scope:
        from kcia.profiles.resolver import resolve_for_task

        return resolve_for_task(
            [repo_root / item for item in scope],
            manifest,
            repo_root,
        )
    from kcia.profiles.resolver import resolve_for_cwd

    return resolve_for_cwd(repo_root, manifest, repo_root)


def _rules_section(resolved: ResolvedProfile) -> str:
    parts = ["### Rules\n"]
    for key, value in resolved.rules.items():
        parts.append(f"- {key}: {value}")
    parts.append("")
    return "\n".join(parts)


def _references_for_wave(
    resolved: ResolvedProfile, wave: WaveDefinition
) -> list[ReferenceEntry]:
    """Filter by tags. `wave.reference_tags is None` => all references."""
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


def _task_statement(session: Session) -> str:
    task = session.task
    statement = (task.get("prompt") or task.get("title") or "").strip()
    ticket_key = (task.get("ticket_key") or "").strip()
    if not statement and not ticket_key:
        return ""

    parts = ["## Task statement\n"]
    if ticket_key:
        parts.append(f"Ticket: `{ticket_key}`\n")
        # In ticket mode the title is just the key; the body comes from ticket.md.
        if statement == ticket_key:
            statement = ""
    if statement:
        parts.append(f"{statement}\n")
    scope = task.get("scope") or []
    if scope:
        parts.append(f"Scope is limited to: {', '.join(scope)}\n")
    return "\n".join(parts)


def _read_context_file(repo_root: Path, name: str) -> str:
    path = context_dir(repo_root) / name
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


