"""Profile pack discovery and loading."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml
from packaging.version import Version

from kcia import VERSION
from kcia.config import USER_DATA_DIR
from kcia.paths import control_plane_root
from kcia.profiles.inheritance import LoadedProfile, ProfileRegistry, UnknownParentError
from kcia.profiles.predicates import PredicateError, validate_predicate
from kcia.profiles.schema import PROFILE_ID_PATTERN, PackSpec, ProfileSpec

PROFILE_FILE = "profile.yaml"
PACK_FILE = "pack.yaml"


@dataclass(frozen=True)
class ProfileSource:
    kind: str
    pack_name: str
    root: Path


def discover_packs(repo_root: Path | None) -> list[ProfileSource]:
    sources: list[ProfileSource] = []

    builtin = control_plane_root() / "profiles"
    if builtin.is_dir():
        sources.append(ProfileSource("builtin", "kcia-builtin", builtin))

    installed_root = USER_DATA_DIR / "packs"
    if installed_root.is_dir():
        for pack_dir in sorted(installed_root.iterdir()):
            if pack_dir.is_dir():
                sources.append(ProfileSource("installed", pack_dir.name, pack_dir))

    user_root = Path.home() / ".config" / "kcia" / "profiles"
    if user_root.is_dir():
        sources.append(ProfileSource("user", "user-profiles", user_root))

    if repo_root is not None:
        repo_profiles = repo_root / ".ai" / "profiles"
        if repo_profiles.is_dir():
            sources.append(ProfileSource("repo", "repo-local", repo_profiles))

    env_path = os.environ.get("KCIA_PROFILE_PATH")
    if env_path:
        override = Path(env_path).expanduser().resolve()
        if override.is_dir():
            pack_name = override.name
            if (override / PACK_FILE).is_file():
                pack_name = _load_pack_name(override)
            sources.append(ProfileSource("env", pack_name, override))

    return sources


def load_registry(repo_root: Path | None, *, strict: bool = False) -> ProfileRegistry:
    profiles: dict[str, LoadedProfile] = {}
    sources: dict[str, str] = {}
    shadowed: list[tuple[str, str]] = []

    for source in discover_packs(repo_root):
        try:
            loaded = _load_source(source, strict=strict)
        except Exception as exc:
            if strict:
                raise
            print(f"warning: skipping pack {source.pack_name}: {exc}")
            continue
        for profile_id, profile in loaded.items():
            if profile_id in profiles:
                shadowed.append((profile_id, sources[profile_id]))
            profiles[profile_id] = profile
            sources[profile_id] = f"{source.kind}:{source.pack_name}"

    _validate_extends(profiles, strict=strict)
    return ProfileRegistry(profiles=profiles, sources=sources, shadowed=shadowed)


def _validate_extends(profiles: dict[str, LoadedProfile], *, strict: bool) -> None:
    for profile_id, loaded in profiles.items():
        parent = loaded.spec.extends
        if parent and parent not in profiles:
            message = (
                f"profile '{profile_id}' extends unknown parent '{parent}'; "
                f"available: {sorted(profiles)}"
            )
            if strict:
                raise UnknownParentError(message)
            print(f"warning: {message}")


def _load_source(source: ProfileSource, *, strict: bool) -> dict[str, LoadedProfile]:
    pack_root = source.root
    pack_spec = _read_pack_spec(pack_root)
    if pack_spec is not None:
        _validate_pack_version(pack_spec)
        profile_ids = pack_spec.profiles
        profile_dirs = [pack_root / profile_id for profile_id in profile_ids]
    else:
        profile_dirs = [
            path
            for path in pack_root.iterdir()
            if path.is_dir() and (path / PROFILE_FILE).is_file()
        ]
        if (pack_root / PROFILE_FILE).is_file():
            profile_dirs.insert(0, pack_root)

    loaded: dict[str, LoadedProfile] = {}
    for profile_dir in profile_dirs:
        profile_path = profile_dir / PROFILE_FILE
        if not profile_path.is_file():
            if strict:
                raise FileNotFoundError(f"missing {PROFILE_FILE} in {profile_dir}")
            continue
        spec = _load_profile_spec(profile_path, strict=strict)
        _validate_profile_assets(spec, profile_dir, strict=strict)
        loaded[spec.id] = LoadedProfile(
            spec=spec,
            root=profile_dir,
            source_kind=source.kind,
            pack_name=source.pack_name,
        )
    return loaded


def _read_pack_spec(pack_root: Path) -> PackSpec | None:
    pack_path = pack_root / PACK_FILE
    if not pack_path.is_file():
        return None
    data = yaml.safe_load(pack_path.read_text(encoding="utf-8"))
    return PackSpec.model_validate(data)


def _load_pack_name(pack_root: Path) -> str:
    pack_spec = _read_pack_spec(pack_root)
    return pack_spec.name if pack_spec else pack_root.name


def _load_profile_spec(profile_path: Path, *, strict: bool) -> ProfileSpec:
    try:
        data = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
        return ProfileSpec.model_validate(data)
    except Exception as exc:
        message = f"{profile_path}: {exc}"
        if strict:
            raise ValueError(message) from exc
        raise


def _validate_pack_version(pack_spec: PackSpec) -> None:
    if Version(pack_spec.kcia_min_version) > Version(VERSION):
        raise ValueError(
            f"pack {pack_spec.name} requires kcia >= {pack_spec.kcia_min_version}, "
            f"but running {VERSION}"
        )


def _validate_profile_assets(spec: ProfileSpec, profile_dir: Path, *, strict: bool) -> None:
    for index, rule in enumerate(spec.detect):
        try:
            validate_predicate(rule.when, path=f"detect[{index}].when")
        except PredicateError as exc:
            raise ValueError(f"{profile_dir / PROFILE_FILE}: {exc}") from exc
    for reference in spec.references:
        if not (profile_dir / reference).is_file():
            raise FileNotFoundError(f"missing reference '{reference}' in {profile_dir}")
    for workflow in spec.workflows:
        if not (profile_dir / workflow).is_file():
            raise FileNotFoundError(f"missing workflow '{workflow}' in {profile_dir}")
    if spec.extends and not PROFILE_ID_PATTERN.match(spec.extends):
        raise ValueError(f"invalid extends id '{spec.extends}'")
