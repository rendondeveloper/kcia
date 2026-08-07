"""Repository initialization: detect profiles, write `.ai/`, render adapters."""

from __future__ import annotations

import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import typer
import yaml

from kcia.config import model_in_catalog, resolve_agents
from kcia.paths import control_plane_root, find_repo_root
from kcia.profiles.bundle import write_bundle, write_if_changed
from kcia.profiles.detector import DetectionHit, detect
from kcia.profiles.inheritance import resolve_inheritance
from kcia.profiles.loader import load_registry
from kcia.project_index import build_project_facts
from kcia.render import render_template

# Everything kcia generates inside a project is regenerable from `kcia init`,
# so none of it belongs in the project's history.
GITIGNORE_MARKER = "# kcia — generated, do not commit"
GITIGNORE_ENTRIES = (
    ".ai/",
    "CLAUDE.md",
    "AGENTS.md",
    ".cursor/rules/",
)


def init(
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Accept all high-confidence detections without asking."
    ),
    path: Path | None = typer.Option(
        None, "--path", help="Repository to initialize (default: current directory)."
    ),
    no_gitignore: bool = typer.Option(
        False, "--no-gitignore", help="Do not touch the project's .gitignore."
    ),
    refresh_context: bool = typer.Option(
        False,
        "--refresh-context",
        help="Regenerate .ai/context/project.md, discarding local edits to it.",
    ),
) -> None:
    """Initialize `.ai/` and generated adapters in the current repository."""
    repo_root = (path or find_repo_root() or Path.cwd()).resolve()
    if not repo_root.is_dir():
        typer.echo(f"not a directory: {repo_root}")
        raise typer.Exit(code=1)

    registry = load_registry(repo_root)
    if not registry.profiles:
        typer.echo("No profiles available. Check the control plane installation.")
        raise typer.Exit(code=1)

    hits = detect(repo_root, registry)
    if not hits:
        typer.echo(
            "No profiles detected. Add a profile under `.ai/profiles/` or run "
            "`kcia profile scaffold <id>`."
        )
        raise typer.Exit(code=1)

    accepted = _resolve_ambiguities(hits, interactive=not yes and sys.stdin.isatty())
    entries = _manifest_entries(accepted)

    written: list[Path] = []
    written += _write_manifest(repo_root, entries)
    written += _write_context(
        repo_root,
        layout="monorepo" if len(entries) > 1 else "single",
        refresh=refresh_context,
    )
    written += _write_bundles(repo_root, registry, entries)
    written += _write_adapters(repo_root, registry, entries)
    if not no_gitignore:
        written += _update_gitignore(repo_root)

    typer.echo(f"Initialized {repo_root}")
    for entry in entries:
        typer.echo(f"  {entry['id']}\t{', '.join(entry['roots'])}")
    if written:
        typer.echo(f"{len(written)} file(s) written.")
    else:
        typer.echo("Already up to date.")

    _report_agents(repo_root)


def _report_agents(repo_root: Path) -> None:
    """Show which agents will run, or how to choose them.

    Agents are what actually execute the waves, so an unconfigured repo would
    otherwise only discover the defaults when a run is already under way.
    """
    resolved = resolve_agents(repo_root)
    unset = [role for role, item in resolved.items() if item.origin == "default"]
    typer.echo("")
    if unset:
        typer.echo("No agents configured yet — waves would run on catalog defaults:")
        for role in unset:
            item = resolved[role]
            typer.echo(f"  {role}: {item.provider}/{item.model}")
        typer.echo("Set them before running the pipeline:")
        typer.echo("  kcia agent models                       see the options")
        typer.echo("  kcia agent set planner claude --model <id>")
        typer.echo("  kcia agent set builder cursor --model <id>")
        return

    typer.echo("Agents:")
    stale = False
    for role, item in resolved.items():
        typer.echo(f"  {role}: {item.provider}/{item.model} ({item.origin})")
        if not model_in_catalog(item.provider, item.model):
            stale = True
            typer.echo(
                f"    warning: `{item.model}` is not offered by `{item.provider}` anymore. "
                f"Run `kcia agent models {item.provider}` and set a valid one."
            )
    if not stale:
        typer.echo("Next: kcia task init \"<what you want>\"")


def _resolve_ambiguities(hits: list[DetectionHit], *, interactive: bool) -> list[DetectionHit]:
    """Keep the best hits per root, asking when a root has several high-confidence ones."""
    by_root: dict[str, list[DetectionHit]] = defaultdict(list)
    for hit in hits:
        by_root[hit.root.as_posix()].append(hit)

    accepted: list[DetectionHit] = []
    for root in sorted(by_root):
        candidates = by_root[root]
        high = [hit for hit in candidates if hit.confidence == "high"]
        chosen = high or _best_per_profile(candidates)
        chosen = _best_per_profile(chosen)

        if len(chosen) > 1 and high and interactive:
            typer.echo(f"\nSeveral profiles match `{root}`:")
            for index, hit in enumerate(chosen, start=1):
                typer.echo(f"  {index}. {hit.profile_id} — {'; '.join(hit.evidence)}")
            answer = typer.prompt(
                "Keep which? (comma-separated numbers, or 'all')", default="all"
            ).strip()
            if answer.lower() != "all":
                picked = []
                for token in answer.split(","):
                    token = token.strip()
                    if token.isdigit() and 1 <= int(token) <= len(chosen):
                        picked.append(chosen[int(token) - 1])
                if picked:
                    chosen = picked
            for hit in chosen:
                accepted.append(_with_resolution(hit, "confirmed"))
            continue

        accepted.extend(_with_resolution(hit, "auto") for hit in chosen)
    return accepted


def _best_per_profile(hits: list[DetectionHit]) -> list[DetectionHit]:
    """Collapse several rules of the same profile at one root to its strongest hit."""
    order = {"high": 3, "medium": 2, "low": 1}
    best: dict[str, DetectionHit] = {}
    for hit in hits:
        current = best.get(hit.profile_id)
        if current is None or order.get(hit.confidence, 0) > order.get(current.confidence, 0):
            best[hit.profile_id] = hit
    return [best[key] for key in sorted(best)]


_RESOLUTIONS: dict[tuple[str, str], str] = {}


def _with_resolution(hit: DetectionHit, resolution: str) -> DetectionHit:
    # DetectionHit is frozen, so the resolution is carried in a side table.
    _RESOLUTIONS[(hit.profile_id, hit.root.as_posix())] = resolution
    return hit


def _root_pattern(root: Path) -> str:
    text = root.as_posix()
    return "**" if text in {".", ""} else f"{text}/**"


def _manifest_entries(hits: list[DetectionHit]) -> list[dict]:
    by_profile: dict[str, dict] = {}
    for hit in hits:
        entry = by_profile.setdefault(
            hit.profile_id,
            {
                "id": hit.profile_id,
                "roots": [],
                "commands": {},
                "detection": {
                    "confidence": hit.confidence,
                    "resolution": _RESOLUTIONS.get(
                        (hit.profile_id, hit.root.as_posix()), "auto"
                    ),
                    "evidence": [],
                },
            },
        )
        pattern = _root_pattern(hit.root)
        if pattern not in entry["roots"]:
            entry["roots"].append(pattern)
        for evidence in hit.evidence:
            if evidence not in entry["detection"]["evidence"]:
                entry["detection"]["evidence"].append(evidence)
    return [by_profile[key] for key in sorted(by_profile)]


def _write_manifest(repo_root: Path, entries: list[dict]) -> list[Path]:
    path = repo_root / ".ai" / "manifest.yaml"
    previous = {}
    if path.is_file():
        previous = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    version_file = control_plane_root() / "VERSION"
    control_plane_version = (
        version_file.read_text(encoding="utf-8").strip() if version_file.is_file() else "0.0.0"
    )

    manifest = {
        "schema_version": 2,
        "project": {
            "name": repo_root.name,
            "default_profile": entries[0]["id"] if entries else None,
            "layout": "monorepo" if len(entries) > 1 else "single",
        },
        "profiles": entries,
        "dependencies": previous.get("dependencies", []),
        "detection": {
            "exclude_dirs": (previous.get("detection") or {}).get("exclude_dirs", []),
            "last_run": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
        "integrations": previous.get(
            "integrations",
            {
                "jira": {"enabled": False, "base_url": None, "project_keys": []},
                "github": {"enabled": True, "allow_open_pr": False, "allow_merge": False},
            },
        ),
        "context": previous.get("context", {"auto_update": True, "requires_active_task": True}),
        "control_plane": {
            "version": control_plane_version,
            "packs": [
                {"name": "kcia-builtin", "version": control_plane_version, "source": "builtin"}
            ],
        },
    }

    # `last_run` changes every run; ignore it when deciding whether anything moved.
    comparable = dict(manifest)
    comparable["detection"] = dict(manifest["detection"])
    comparable["detection"].pop("last_run", None)
    prior = dict(previous)
    if prior:
        prior["detection"] = dict(prior.get("detection") or {})
        prior["detection"].pop("last_run", None)
    if prior == comparable:
        return []

    content = yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return [path]


def _write_context(repo_root: Path, *, layout: str, refresh: bool = False) -> list[Path]:
    templates = control_plane_root() / "templates"
    path = repo_root / ".ai" / "context" / "project.md"
    # The file is yours once it exists: it is the one place to add what only a
    # human knows. `--refresh-context` opts back into the generated version.
    if path.is_file() and not refresh:
        return []

    facts = build_project_facts(repo_root, name=repo_root.name, layout=layout)
    if not facts:
        facts = (
            "## Summary\nUNKNOWN — kcia could not derive the stack from this repository.\n"
            "Describe it here; this section is injected into every wave."
        )
    content = render_template(templates, "project.md.j2", facts=facts)
    return [path] if write_if_changed(path, content) else []


def _write_bundles(repo_root: Path, registry, entries: list[dict]) -> list[Path]:
    written: list[Path] = []
    for entry in entries:
        resolved = resolve_inheritance(entry["id"], registry)
        written.extend(write_bundle(repo_root, resolved, entry["roots"]))
    return written


def _write_adapters(repo_root: Path, registry, entries: list[dict]) -> list[Path]:
    templates = control_plane_root() / "templates" / "adapters"
    written: list[Path] = []
    profiles = [{"id": entry["id"], "roots": entry["roots"]} for entry in entries]

    for name in ("CLAUDE.md", "AGENTS.md"):
        content = render_template(templates, f"{name}.j2", profiles=profiles)
        if write_if_changed(repo_root / name, content):
            written.append(repo_root / name)

    rules_dir = repo_root / ".cursor" / "rules"
    core = render_template(templates / "cursor", "00-core.mdc.j2")
    if write_if_changed(rules_dir / "00-core.mdc", core):
        written.append(rules_dir / "00-core.mdc")

    for index, entry in enumerate(entries, start=1):
        resolved = resolve_inheritance(entry["id"], registry)
        cursor_globs = (resolved.adapters.get("cursor") or {}).get("globs") or ["**/*"]
        globs = _expand_globs(entry["roots"], cursor_globs)
        content = render_template(
            templates / "cursor",
            "profile.mdc.j2",
            profile={
                "id": resolved.id,
                "display_name": resolved.display_name,
                "roots": entry["roots"],
            },
            globs=globs,
        )
        path = rules_dir / f"{index:02d}-{resolved.id}.mdc"
        if write_if_changed(path, content):
            written.append(path)

        if (resolved.adapters.get("claude") or {}).get("nested_file"):
            for root in entry["roots"]:
                directory = repo_root / root.removesuffix("/**")
                if root == "**" or not directory.is_dir():
                    continue
                nested = render_template(templates, "CLAUDE.md.j2", profiles=[
                    {"id": resolved.id, "roots": [root]}
                ])
                if write_if_changed(directory / "CLAUDE.md", nested):
                    written.append(directory / "CLAUDE.md")

    return written


def _expand_globs(roots: list[str], profile_globs: list[str]) -> list[str]:
    globs: list[str] = []
    for root in roots:
        prefix = root.removesuffix("/**")
        for glob in profile_globs:
            combined = glob if root == "**" else f"{prefix}/{glob}"
            if combined not in globs:
                globs.append(combined)
    return globs


def _update_gitignore(repo_root: Path) -> list[Path]:
    """Add every kcia-generated path to the project's .gitignore."""
    path = repo_root / ".gitignore"
    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    present = {line.strip() for line in existing.splitlines()}
    missing = [entry for entry in GITIGNORE_ENTRIES if entry not in present]
    if not missing:
        return []

    block = "" if not existing or existing.endswith("\n") else "\n"
    if GITIGNORE_MARKER not in existing:
        block += f"\n{GITIGNORE_MARKER}\n"
    block += "".join(f"{entry}\n" for entry in missing)
    path.write_text(existing + block, encoding="utf-8")
    return [path]
