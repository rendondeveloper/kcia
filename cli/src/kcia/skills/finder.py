"""Search the project for skill files by name."""

from __future__ import annotations

from pathlib import Path

from kcia.waves.progress import StepProgress


def personal_skill_hint(name: str) -> Path | None:
    home = Path.home() / ".cursor" / "skills" / name / "SKILL.md"
    if home.is_file():
        return home
    alt = Path.home() / ".cursor" / "skills" / f"{name}.md"
    if alt.is_file():
        return alt
    return None


def project_skill_candidates(repo_root: Path, name: str) -> list[Path]:
    candidates = [
        repo_root / ".cursor" / "skills" / name / "SKILL.md",
        repo_root / ".cursor" / "skills" / f"{name}.md",
        repo_root / ".claude" / "skills" / name / "SKILL.md",
    ]
    return [path for path in candidates if path.is_file()]


def resolve_skill_path(path: Path, repo_root: Path) -> Path | None:
    """Resolve a user path to a skill file."""
    target = path.expanduser()
    if not target.is_absolute():
        target = (repo_root / target).resolve()
    else:
        target = target.resolve()

    if target.is_file() and target.name == "SKILL.md":
        return target
    if target.is_dir():
        skill = target / "SKILL.md"
        if skill.is_file():
            return skill
    return None


def skill_name_from_path(skill_file: Path) -> str:
    if skill_file.name == "SKILL.md":
        return skill_file.parent.name
    return skill_file.stem


def find_skill_by_name(
    repo_root: Path,
    name: str,
    *,
    stream=None,
) -> tuple[Path | None, list[Path], Path | None]:
    """Search the project for a skill name.

    Returns `(found_path, ambiguous_hits, personal_hint)`.
    """
    with StepProgress(f"searching for skill {name!r}", stream=stream):
        hits = project_skill_candidates(repo_root, name)
        if len(hits) == 1:
            return hits[0], [], None
        if len(hits) > 1:
            return None, hits, None
        hint = personal_skill_hint(name)
        return None, [], hint
