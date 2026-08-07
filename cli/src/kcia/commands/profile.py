"""Profile pack management commands."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Optional

import typer
import yaml

from kcia.paths import find_repo_root
from kcia.profiles.detector import detect
from kcia.profiles.inheritance import (
    CircularInheritanceError,
    InheritanceTooDeepError,
    UnknownParentError,
    resolve_inheritance,
)
from kcia.profiles.loader import discover_packs, load_registry
from kcia.profiles.resolver import resolve_for_path
from kcia.profiles.schema import PROFILE_ID_PATTERN, ProfileSpec

app = typer.Typer(help="Discover and manage profile packs.", no_args_is_help=True)


@app.command("list")
def profile_list(
    sources: bool = typer.Option(False, "--sources", help="Show pack source per profile."),
    show_all: bool = typer.Option(False, "--all", help="Include abstract profiles."),
) -> None:
    repo_root = find_repo_root()
    registry = load_registry(repo_root)
    for profile_id in sorted(registry.profiles):
        loaded = registry.profiles[profile_id]
        if loaded.spec.abstract and not show_all:
            continue
        line = f"{profile_id}\t{loaded.spec.display_name}"
        if sources:
            line += f"\t{registry.sources.get(profile_id, 'unknown')}"
        typer.echo(line)


@app.command("show")
def profile_show(profile_id: str) -> None:
    repo_root = find_repo_root()
    registry = load_registry(repo_root)
    resolved = resolve_inheritance(profile_id, registry)
    typer.echo(f"id: {resolved.id}")
    typer.echo(f"display_name: {resolved.display_name}")
    if resolved.ancestors:
        typer.echo(f"extends: {' -> '.join([*resolved.ancestors, resolved.id])}")
    typer.echo("commands:")
    for key, value in resolved.commands.items():
        typer.echo(f"  {key}: {value}")
    typer.echo("rules:")
    for key, value in resolved.rules.items():
        typer.echo(f"  {key}: {value}")
    typer.echo("references:")
    for owner, path in resolved.references:
        typer.echo(f"  [{owner}] {path.name}")


@app.command("explain")
def profile_explain(path: str) -> None:
    repo_root = find_repo_root() or Path.cwd()
    manifest_path = repo_root / ".ai" / "manifest.yaml"
    if not manifest_path.is_file():
        typer.echo("No `.ai/manifest.yaml` found. Run `kcia init` first.")
        raise typer.Exit(code=1)
    from kcia.profiles.schema import Manifest

    manifest = Manifest.model_validate(yaml.safe_load(manifest_path.read_text(encoding="utf-8")))
    target = (repo_root / path).resolve()
    active = resolve_for_path(target, manifest, repo_root=repo_root)
    if not active:
        typer.echo(f"No active profiles for `{path}`.")
        raise typer.Exit(code=1)
    registry = load_registry(repo_root)
    for profile_id in active:
        entry = next(item for item in manifest.profiles if item.id == profile_id)
        typer.echo(f"{profile_id}")
        for root in entry.roots:
            typer.echo(f"  root: {root}")
        detection = entry.detection or {}
        for item in detection.get("evidence", []):
            typer.echo(f"  evidence: {item}")
        if profile_id in registry.profiles:
            typer.echo(f"  source: {registry.sources[profile_id]}")


@app.command("detect")
def profile_detect(
    dry_run: bool = typer.Option(False, "--dry-run", help="Only print detection results."),
) -> None:
    repo_root = find_repo_root() or Path.cwd()
    registry = load_registry(repo_root)
    hits = detect(repo_root, registry)
    if not hits:
        typer.echo("No profiles detected. Add a pubspec.yaml, package.json, or run `kcia profile scaffold`.")
        raise typer.Exit(code=1)
    for hit in hits:
        typer.echo(
            f"{hit.profile_id}\t{hit.root}\t{hit.confidence}\t{'; '.join(hit.evidence)}"
        )
    if dry_run:
        return


@app.command("add")
def profile_add(
    source: str = typer.Argument(..., help="Git URL or local path to a profile pack."),
    name: Optional[str] = typer.Option(None, "--name", help="Installed pack directory name."),
) -> None:
    source_path = Path(source).expanduser()
    if source_path.exists():
        pack_root = source_path.resolve()
    else:
        typer.echo("NOT IMPLEMENTED: git-based pack install is pending; use a local path for now.")
        raise typer.Exit(code=1)
    pack_name = name or pack_root.name
    target = Path.home() / ".local" / "share" / "kcia" / "packs" / pack_name
    if target.exists():
        typer.echo(f"Pack `{pack_name}` already exists at {target}")
        raise typer.Exit(code=1)
    shutil.copytree(pack_root, target)
    typer.echo(f"Installed pack `{pack_name}` to {target}")


@app.command("remove")
def profile_remove(pack_name: str) -> None:
    target = Path.home() / ".local" / "share" / "kcia" / "packs" / pack_name
    if not target.exists():
        typer.echo(f"Pack `{pack_name}` not found.")
        raise typer.Exit(code=1)
    shutil.rmtree(target)
    typer.echo(f"Removed pack `{pack_name}`.")


@app.command("validate")
def profile_validate(
    path: Optional[str] = typer.Argument(None, help="Path to a profile pack to validate."),
) -> None:
    pack_root = Path(path).expanduser().resolve() if path else None
    if pack_root is None:
        typer.echo("validate requires a pack path during bootstrap.")
        raise typer.Exit(code=1)
    previous = os.environ.get("KCIA_PROFILE_PATH")
    os.environ["KCIA_PROFILE_PATH"] = str(pack_root)
    try:
        registry = load_registry(None, strict=True)
    finally:
        if previous is None:
            os.environ.pop("KCIA_PROFILE_PATH", None)
        else:
            os.environ["KCIA_PROFILE_PATH"] = previous
    typer.echo(f"Pack at {pack_root} is valid ({len(registry.profiles)} profiles).")


@app.command("scaffold")
def profile_scaffold(
    profile_id: str = typer.Argument(...),
    extends: Optional[str] = typer.Option(None, "--extends"),
) -> None:
    if not PROFILE_ID_PATTERN.match(profile_id):
        typer.echo(f"Invalid profile id `{profile_id}`.")
        raise typer.Exit(code=1)
    target = Path.cwd() / profile_id
    if target.exists():
        typer.echo(f"Directory `{target}` already exists.")
        raise typer.Exit(code=1)
    target.mkdir(parents=True)
    (target / "references").mkdir()
    (target / "workflows").mkdir()
    spec = {
        "schema_version": 2,
        "id": profile_id,
        "display_name": profile_id.replace("-", " ").title(),
        "description": "TODO: describe this profile.",
        "abstract": False,
        "extends": extends,
        "detect": [
            {
                "when": {"file_exists": "README.md"},
                "confidence": "low",
                "evidence": "TODO: replace with real detection rules.",
            }
        ],
        "commands": {
            "install": "echo TODO-install",
            "test": "echo TODO-test",
            "lint": "echo TODO-lint",
            "verify": "echo TODO-verify",
        },
        "references": ["references/coding.md"],
        "workflows": [
            "workflows/feature.md",
            "workflows/bugfix.md",
            "workflows/refactor.md",
            "workflows/pr-review.md",
        ],
        "rules": {"require_tests_for_code_changes": True},
        "validation": {"required_commands": ["test", "lint"]},
    }
    (target / "profile.yaml").write_text(
        yaml.safe_dump(spec, sort_keys=False),
        encoding="utf-8",
    )
    (target / "references" / "coding.md").write_text(
        "# Coding standards\n\nTODO: document coding standards.\n",
        encoding="utf-8",
    )
    for workflow in ["feature", "bugfix", "refactor", "pr-review"]:
        (target / "workflows" / f"{workflow}.md").write_text(
            f"# {workflow.title()} workflow\n\nTODO: document workflow.\n",
            encoding="utf-8",
        )
    typer.echo(f"Scaffolded profile at {target}")


def format_inheritance_error(exc: Exception) -> str:
    if isinstance(exc, CircularInheritanceError):
        return str(exc)
    if isinstance(exc, InheritanceTooDeepError):
        return str(exc)
    if isinstance(exc, UnknownParentError):
        return str(exc)
    return str(exc)
