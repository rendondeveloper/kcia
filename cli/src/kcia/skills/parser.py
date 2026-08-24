"""Parse `kcia skill` argv into a structured invocation."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from kcia.skills.constants import NAMESPACE_FLAGS


class SkillAction(str, Enum):
    LIST = "list"
    REGISTER = "register"
    REMOVE = "remove"
    RUN = "run"


@dataclass
class ParsedSkillArgs:
    action: SkillAction
    namespaces: list[str] = field(default_factory=list)
    path: str | None = None
    remove: bool = False
    shortcuts: list[str] = field(default_factory=list)
    names: list[str] = field(default_factory=list)
    extra_argv: list[str] = field(default_factory=list)


def normalize_flag(flag: str) -> str:
    return flag.replace("-", "_")


def parse_skill_argv(raw: list[str]) -> ParsedSkillArgs:
    namespaces: list[str] = []
    path: str | None = None
    remove = False
    shortcuts: list[str] = []
    names: list[str] = []
    extra_argv: list[str] = []
    first_name_index: int | None = None
    first_shortcut_index: int | None = None

    index = 0
    while index < len(raw):
        arg = raw[index]
        if arg == "--":
            extra_argv.extend(raw[index + 1 :])
            break
        if not arg.startswith("--"):
            names.append(arg)
            if first_name_index is None:
                first_name_index = index
            index += 1
            continue

        flag = normalize_flag(arg[2:])
        if flag in NAMESPACE_FLAGS:
            namespaces.append(flag)
            index += 1
            continue
        if flag == "path":
            if index + 1 >= len(raw):
                raise ValueError("--path requires a value")
            path = raw[index + 1]
            index += 2
            continue
        if flag == "remove":
            remove = True
            index += 1
            continue
        shortcuts.append(flag)
        if first_shortcut_index is None:
            first_shortcut_index = index
        index += 1

    if path is not None and remove:
        raise ValueError("Pass either --path or --remove, not both")

    if remove and shortcuts:
        return ParsedSkillArgs(
            action=SkillAction.REMOVE,
            namespaces=namespaces,
            remove=True,
            shortcuts=shortcuts,
            names=names,
            extra_argv=extra_argv,
        )

    if path is not None:
        return ParsedSkillArgs(
            action=SkillAction.REGISTER,
            namespaces=namespaces,
            path=path,
            shortcuts=shortcuts,
            names=names,
            extra_argv=extra_argv,
        )

    if shortcuts and names:
        name_first = (
            first_name_index is not None
            and first_shortcut_index is not None
            and first_name_index < first_shortcut_index
        )
        if name_first:
            return ParsedSkillArgs(
                action=SkillAction.REGISTER,
                namespaces=namespaces,
                shortcuts=shortcuts,
                names=names,
                extra_argv=extra_argv,
            )
        return ParsedSkillArgs(
            action=SkillAction.RUN,
            namespaces=namespaces,
            shortcuts=shortcuts,
            extra_argv=names + extra_argv,
        )

    if shortcuts:
        return ParsedSkillArgs(
            action=SkillAction.RUN,
            namespaces=namespaces,
            shortcuts=shortcuts,
            extra_argv=extra_argv,
        )

    if names:
        return ParsedSkillArgs(
            action=SkillAction.REGISTER,
            namespaces=namespaces,
            names=names,
            extra_argv=extra_argv,
        )

    return ParsedSkillArgs(action=SkillAction.LIST, namespaces=namespaces)
