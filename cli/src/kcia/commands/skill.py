"""Register, list, remove, and run local skill shortcuts."""

from __future__ import annotations

from pathlib import Path

import click
import typer
from typer.core import TyperGroup

from kcia.cancel import interruptible
from kcia.paths import find_repo_root
from kcia.skills.catalog import (
    find_shortcut_namespaces,
    get_entry,
    is_valid_shortcut,
    load_catalog,
    namespace_entries,
    namespaces_with_entries,
    remove_entry,
    save_catalog,
    set_entry,
    store_path,
)
from kcia.skills.family import namespace_available
from kcia.skills.finder import (
    find_skill_by_name,
    resolve_skill_path,
    skill_name_from_path,
)
from kcia.skills.parser import ParsedSkillArgs, SkillAction, parse_skill_argv
from kcia.skills.runner import run_cataloged_skill
from kcia.skills.schema import SkillEntry
from kcia.waves.progress import StepProgress
from kcia.waves.runner import check_agents_ready
from kcia.waves.session import Session, session_path


class SkillGroup(TyperGroup):
    """Capture argv before Click treats dynamic shortcut flags as options."""

    @staticmethod
    def _state(ctx: click.Context) -> dict:
        root = ctx
        while root.parent is not None:
            root = root.parent
        root.ensure_object(dict)
        return root.obj

    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        if not args and self.no_args_is_help and not ctx.resilient_parsing:
            raise click.NoArgsIsHelpError(ctx)
        state = self._state(ctx)
        state["skill_args"] = list(args)
        ctx._protected_args = []
        ctx.args = []
        return []


app = typer.Typer(
    help="Register and run local skill shortcuts from `.ai/skills.yaml`.",
    invoke_without_command=True,
    cls=SkillGroup,
)


def _require_repo() -> Path:
    repo = find_repo_root()
    if repo is None:
        typer.echo("No git repository found.")
        raise typer.Exit(code=1)
    if not (repo / ".ai").is_dir():
        typer.echo("No `.ai/` directory found. Run `kcia init` first.")
        raise typer.Exit(code=1)
    return repo


def _require_namespace(repo: Path, namespace: str) -> None:
    if not namespace_available(repo, namespace):
        typer.echo(
            f"Profile namespace `{namespace}` is not active in this repository. "
            "Run `kcia init` or check `.ai/manifest.yaml`."
        )
        raise typer.Exit(code=1)


def _resolve_namespace(parsed: ParsedSkillArgs) -> str | None:
    if len(parsed.namespaces) > 1:
        typer.echo(
            "Pass only one namespace flag: --backend, --mobile, --web, or --commons."
        )
        raise typer.Exit(code=1)
    return parsed.namespaces[0] if parsed.namespaces else None


def _resolve_shortcut_for_action(
    repo: Path,
    parsed: ParsedSkillArgs,
    catalog,
    *,
    require_namespace: bool,
) -> tuple[str, str]:
    if len(parsed.shortcuts) > 1:
        typer.echo("Pass only one skill shortcut per command.")
        raise typer.Exit(code=1)

    namespace = _resolve_namespace(parsed)
    shortcut = parsed.shortcuts[0] if parsed.shortcuts else None

    if shortcut is None:
        typer.echo("Pass a skill shortcut flag (for example `--deploy`).")
        raise typer.Exit(code=1)

    if namespace is None:
        hits = find_shortcut_namespaces(catalog, shortcut)
        if len(hits) == 1:
            namespace = hits[0]
        elif len(hits) > 1:
            joined = ", ".join(f"--{hit}" for hit in hits)
            typer.echo(
                f"Shortcut `{shortcut}` is registered in multiple namespaces ({joined}). "
                "Pass the profile namespace flag to choose one."
            )
            raise typer.Exit(code=1)
        else:
            typer.echo(
                f"Unknown skill shortcut `{shortcut}`. "
                "Register it first with `kcia skill --<namespace> --path <skill> --<shortcut>`."
            )
            raise typer.Exit(code=1)
    else:
        if require_namespace and namespace != "commons":
            _require_namespace(repo, namespace)
        if get_entry(catalog, namespace, shortcut) is None:
            typer.echo(
                f"Unknown skill shortcut `{shortcut}` in namespace `{namespace}`. "
                "Register it first with `kcia skill --<namespace> --path <skill> --<shortcut>`."
            )
            raise typer.Exit(code=1)

    return namespace, shortcut


def _derive_shortcut(name: str, explicit: str | None) -> str:
    if explicit is not None:
        if not is_valid_shortcut(explicit):
            typer.echo(
                f"Shortcut `{explicit}` is invalid or reserved. "
                "Use lowercase letters, digits, and underscores."
            )
            raise typer.Exit(code=1)
        return explicit
    if is_valid_shortcut(name):
        return name
    typer.echo(
        f"Skill name `{name}` is not a valid shortcut. "
        "Pass an explicit shortcut flag (for example `--deploy`)."
    )
    raise typer.Exit(code=1)


def _list_catalog(repo: Path, parsed: ParsedSkillArgs) -> None:
    catalog = load_catalog(repo)
    namespace = _resolve_namespace(parsed)

    if namespace is None:
        namespaces = namespaces_with_entries(catalog)
        if not namespaces:
            typer.echo(
                "No skills registered. Use `kcia skill --<namespace> --path <skill> --<shortcut>`."
            )
            return
        for item in namespaces:
            _print_namespace(catalog, item)
        return

    if namespace != "commons":
        _require_namespace(repo, namespace)
    entries = namespace_entries(catalog, namespace)
    if not entries:
        typer.echo(f"No skills registered under `{namespace}`.")
        return
    _print_namespace(catalog, namespace)


def _print_namespace(catalog, namespace: str) -> None:
    entries = namespace_entries(catalog, namespace)
    if not entries:
        return
    typer.echo(f"[{namespace}]")
    for shortcut, entry in sorted(entries.items()):
        typer.echo(f"  {shortcut}: {entry.name} ({entry.path})")


def _register_skill(repo: Path, parsed: ParsedSkillArgs) -> None:
    namespace = _resolve_namespace(parsed)
    if namespace is None:
        typer.echo(
            "Register requires a namespace: --backend, --mobile, --web, or --commons."
        )
        raise typer.Exit(code=1)
    if namespace != "commons":
        _require_namespace(repo, namespace)

    explicit_shortcut = parsed.shortcuts[0] if parsed.shortcuts else None
    skill_file: Path | None = None
    skill_name: str | None = None

    if parsed.path is not None:
        with StepProgress("validating skill path"):
            skill_file = resolve_skill_path(Path(parsed.path), repo)
        if skill_file is None:
            typer.echo(f"Skill path not found or missing SKILL.md: {parsed.path}")
            raise typer.Exit(code=1)
        skill_name = skill_name_from_path(skill_file)
        if parsed.names:
            skill_name = parsed.names[0]
    elif parsed.names:
        found, ambiguous, hint = find_skill_by_name(repo, parsed.names[0])
        if ambiguous:
            typer.echo(f"Multiple skills match `{parsed.names[0]}`:")
            for path in ambiguous:
                typer.echo(f"  {path}")
            typer.echo("Pass `--path` to choose one.")
            raise typer.Exit(code=1)
        if found is None:
            typer.echo(f"Skill `{parsed.names[0]}` was not found in this project.")
            if hint is not None:
                typer.echo(f"Personal skill found at {hint} — pass `--path` to register it.")
            raise typer.Exit(code=1)
        skill_file = found
        skill_name = parsed.names[0]
    else:
        typer.echo("Register requires `--path` or a skill name.")
        raise typer.Exit(code=1)

    shortcut = _derive_shortcut(skill_name, explicit_shortcut)
    catalog = load_catalog(repo)
    if get_entry(catalog, namespace, shortcut) is not None:
        typer.echo(
            f"`{namespace}/{shortcut}` is already registered. "
            "Remove it with `--remove`, then register again."
        )
        raise typer.Exit(code=1)

    entry = SkillEntry(name=skill_name, path=store_path(repo, skill_file))
    set_entry(catalog, namespace, shortcut, entry)
    save_catalog(repo, catalog)
    typer.echo(f"Registered `{namespace}/{shortcut}` → {entry.path}")


def _remove_skill(repo: Path, parsed: ParsedSkillArgs) -> None:
    catalog = load_catalog(repo)
    namespace, shortcut = _resolve_shortcut_for_action(
        repo, parsed, catalog, require_namespace=True
    )
    if namespace != "commons":
        _require_namespace(repo, namespace)

    if not remove_entry(catalog, namespace, shortcut):
        typer.echo(f"No skill `{namespace}/{shortcut}` is registered.")
        raise typer.Exit(code=1)

    save_catalog(repo, catalog)
    typer.echo(f"Removed `{namespace}/{shortcut}`.")


def _check_terminal_free(repo: Path) -> None:
    path = session_path(repo)
    if not path.is_file():
        return
    session = Session.load(repo)
    session.clear_stale_lock()
    if session.is_locked():
        lock = session.data.get("lock", {})
        typer.echo(
            f"Another wave is running (pid {lock.get('pid')}, acquired {lock.get('acquired_at')})."
        )
        raise typer.Exit(code=1)


def _run_skill(repo: Path, parsed: ParsedSkillArgs) -> None:
    catalog = load_catalog(repo)
    namespace, shortcut = _resolve_shortcut_for_action(
        repo, parsed, catalog, require_namespace=True
    )
    if namespace != "commons":
        _require_namespace(repo, namespace)

    entry = get_entry(catalog, namespace, shortcut)
    if entry is None:
        typer.echo(f"No skill `{namespace}/{shortcut}` is registered.")
        raise typer.Exit(code=1)

    skill_path = Path(entry.path)
    if not skill_path.is_absolute():
        skill_path = (repo / skill_path).resolve()
    if not skill_path.is_file():
        typer.echo(f"Skill file missing: {entry.path}")
        raise typer.Exit(code=1)

    _check_terminal_free(repo)

    problems = check_agents_ready(repo)
    if problems:
        typer.echo("Cannot start — the configured agents are not ready:")
        for problem in problems:
            typer.echo(f"  - {problem}")
        typer.echo("Run `kcia doctor` for the full picture.")
        raise typer.Exit(code=1)

    with interruptible() as cancel:
        exit_code, output = run_cataloged_skill(
            repo,
            namespace,
            shortcut,
            skill_path,
            parsed.extra_argv,
            should_cancel=cancel,
        )

    if output:
        typer.echo(output)
    raise typer.Exit(code=exit_code)


def _dispatch(parsed: ParsedSkillArgs, repo: Path) -> None:
    if parsed.action == SkillAction.LIST:
        _list_catalog(repo, parsed)
        return
    if parsed.action == SkillAction.REGISTER:
        _register_skill(repo, parsed)
        return
    if parsed.action == SkillAction.REMOVE:
        _remove_skill(repo, parsed)
        return
    if parsed.action == SkillAction.RUN:
        _run_skill(repo, parsed)
        return
    typer.echo("Nothing to do.")
    raise typer.Exit(code=1)


@app.callback()
def skill_main(ctx: typer.Context) -> None:
    """Register, list, remove, and run local skill shortcuts."""
    state = SkillGroup._state(ctx)
    raw_args = state.get("skill_args", list(ctx.args))
    try:
        parsed = parse_skill_argv(raw_args)
    except ValueError as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1) from exc

    repo = _require_repo()
    _dispatch(parsed, repo)
