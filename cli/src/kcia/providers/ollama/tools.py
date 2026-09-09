"""Tool schemas and executors for the Ollama agent loop."""

from __future__ import annotations

import fnmatch
import json
import re
import subprocess
from pathlib import Path
from typing import Any

import pathspec

from kcia.providers.base import RunRequest

READ_TOOLS = frozenset({"read_file", "list_dir", "grep", "glob"})
WRITE_TOOLS = frozenset({"write_file", "edit_file"})
SHELL_TOOLS = frozenset({"run_command"})


def tool_definitions(req: RunRequest) -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = [
        _tool(
            "read_file",
            "Read a UTF-8 text file relative to the workspace root.",
            {"path": {"type": "string", "description": "Relative file path."}},
            required=["path"],
        ),
        _tool(
            "list_dir",
            "List files and directories under a relative path.",
            {
                "path": {
                    "type": "string",
                    "description": "Relative directory path (default: .).",
                }
            },
        ),
        _tool(
            "grep",
            "Search for a regex pattern in files under a path.",
            {
                "pattern": {"type": "string", "description": "Regular expression."},
                "path": {
                    "type": "string",
                    "description": "Relative file or directory path (default: .).",
                },
            },
            required=["pattern"],
        ),
        _tool(
            "glob",
            "Find files matching a glob pattern relative to the workspace root.",
            {
                "pattern": {
                    "type": "string",
                    "description": "Glob pattern, e.g. **/*.py",
                }
            },
            required=["pattern"],
        ),
    ]
    if req.allow_edits:
        tools.extend(
            [
                _tool(
                    "write_file",
                    "Create or overwrite a UTF-8 text file.",
                    {
                        "path": {"type": "string", "description": "Relative file path."},
                        "content": {"type": "string", "description": "Full file content."},
                    },
                    required=["path", "content"],
                ),
                _tool(
                    "edit_file",
                    "Replace one exact occurrence in a UTF-8 text file.",
                    {
                        "path": {"type": "string", "description": "Relative file path."},
                        "old_string": {
                            "type": "string",
                            "description": "Exact text to replace.",
                        },
                        "new_string": {
                            "type": "string",
                            "description": "Replacement text.",
                        },
                    },
                    required=["path", "old_string", "new_string"],
                ),
                _tool(
                    "run_command",
                    "Run a shell command in the workspace root.",
                    {
                        "command": {
                            "type": "string",
                            "description": "Shell command to execute.",
                        }
                    },
                    required=["command"],
                ),
            ]
        )
    return tools


def execute_tool(name: str, raw_args: Any, req: RunRequest) -> tuple[bool, str]:
    if not isinstance(raw_args, dict):
        return False, "tool arguments must be a JSON object"

    if name in WRITE_TOOLS or name in SHELL_TOOLS:
        if not req.allow_edits:
            return False, f"tool '{name}' is not allowed when edits are disabled"

    try:
        if name == "read_file":
            return _read_file(raw_args, req)
        if name == "list_dir":
            return _list_dir(raw_args, req)
        if name == "grep":
            return _grep(raw_args, req)
        if name == "glob":
            return _glob(raw_args, req)
        if name == "write_file":
            return _write_file(raw_args, req)
        if name == "edit_file":
            return _edit_file(raw_args, req)
        if name == "run_command":
            return _run_command(raw_args, req)
    except Exception as exc:  # noqa: BLE001 — return tool errors to the model
        return False, str(exc)

    return False, f"unknown tool '{name}'"


def safe_path(root: Path, relative: str) -> Path | None:
    candidate = (root / relative).resolve()
    root_resolved = root.resolve()
    if root_resolved not in candidate.parents and candidate != root_resolved:
        return None
    return candidate


def relative_path(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def in_edit_scope(rel_path: str, edit_scope: tuple[str, ...] | None) -> bool:
    if not edit_scope:
        return True
    spec = pathspec.PathSpec.from_lines("gitwildmatch", list(edit_scope))
    return spec.match_file(rel_path)


def _tool(
    name: str,
    description: str,
    properties: dict[str, dict[str, str]],
    *,
    required: list[str] | None = None,
) -> dict[str, Any]:
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": properties,
    }
    if required:
        parameters["required"] = required
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": parameters,
        },
    }


def _require_str(args: dict[str, Any], key: str) -> str | None:
    value = args.get(key)
    if not isinstance(value, str) or not value:
        return None
    return value


def _resolve_read_path(args: dict[str, Any], req: RunRequest) -> tuple[Path | None, str | None]:
    rel = _require_str(args, "path") or "."
    resolved = safe_path(req.cwd, rel)
    if resolved is None:
        return None, f"path escapes workspace: {rel}"
    return resolved, rel


def _read_file(args: dict[str, Any], req: RunRequest) -> tuple[bool, str]:
    path, error = _resolve_read_path(args, req)
    if path is None:
        return False, error or "invalid path"
    if not path.is_file():
        return False, f"not a file: {args.get('path')}"
    try:
        return True, path.read_text(encoding="utf-8")
    except OSError as exc:
        return False, str(exc)


def _list_dir(args: dict[str, Any], req: RunRequest) -> tuple[bool, str]:
    path, error = _resolve_read_path(args, req)
    if path is None:
        return False, error or "invalid path"
    if not path.is_dir():
        return False, f"not a directory: {args.get('path', '.')}"
    entries = sorted(child.name + ("/" if child.is_dir() else "") for child in path.iterdir())
    return True, "\n".join(entries) if entries else "(empty)"


def _grep(args: dict[str, Any], req: RunRequest) -> tuple[bool, str]:
    pattern = _require_str(args, "pattern")
    if pattern is None:
        return False, "missing required argument: pattern"
    path, error = _resolve_read_path(args, req)
    if path is None:
        return False, error or "invalid path"
    try:
        regex = re.compile(pattern)
    except re.error as exc:
        return False, f"invalid regex: {exc}"

    matches: list[str] = []
    files = [path] if path.is_file() else [p for p in path.rglob("*") if p.is_file()]
    for file_path in files:
        try:
            text = file_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        rel = relative_path(file_path, req.cwd)
        for line_no, line in enumerate(text.splitlines(), start=1):
            if regex.search(line):
                matches.append(f"{rel}:{line_no}:{line}")
    return True, "\n".join(matches) if matches else "(no matches)"


def _glob(args: dict[str, Any], req: RunRequest) -> tuple[bool, str]:
    pattern = _require_str(args, "pattern")
    if pattern is None:
        return False, "missing required argument: pattern"
    root = req.cwd.resolve()
    matches: list[str] = []
    for path in root.rglob("*"):
        rel = relative_path(path, root)
        if fnmatch.fnmatch(rel, pattern):
            matches.append(rel)
    return True, "\n".join(sorted(matches)) if matches else "(no matches)"


def _resolve_write_path(args: dict[str, Any], req: RunRequest) -> tuple[Path | None, str | None]:
    rel = _require_str(args, "path")
    if rel is None:
        return None, "missing required argument: path"
    if not in_edit_scope(rel, req.edit_scope):
        return None, f"path not in edit scope: {rel}"
    resolved = safe_path(req.cwd, rel)
    if resolved is None:
        return None, f"path escapes workspace: {rel}"
    return resolved, rel


def _write_file(args: dict[str, Any], req: RunRequest) -> tuple[bool, str]:
    path, error = _resolve_write_path(args, req)
    if path is None:
        return False, error or "invalid path"
    content = args.get("content")
    if not isinstance(content, str):
        return False, "missing required argument: content"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return True, f"wrote {args['path']}"


def _edit_file(args: dict[str, Any], req: RunRequest) -> tuple[bool, str]:
    path, error = _resolve_write_path(args, req)
    if path is None:
        return False, error or "invalid path"
    old_string = args.get("old_string")
    new_string = args.get("new_string")
    if not isinstance(old_string, str) or not isinstance(new_string, str):
        return False, "missing required arguments: old_string, new_string"
    if not path.is_file():
        return False, f"not a file: {args.get('path')}"
    text = path.read_text(encoding="utf-8")
    if old_string not in text:
        return False, "old_string not found in file"
    path.write_text(text.replace(old_string, new_string, 1), encoding="utf-8")
    return True, f"edited {args['path']}"


def _run_command(args: dict[str, Any], req: RunRequest) -> tuple[bool, str]:
    command = _require_str(args, "command")
    if command is None:
        return False, "missing required argument: command"
    completed = subprocess.run(
        command,
        shell=True,
        cwd=str(req.cwd),
        capture_output=True,
        text=True,
        timeout=300,
    )
    output = "".join(part for part in (completed.stdout, completed.stderr) if part)
    if completed.returncode == 0:
        return True, output or "(no output)"
    return False, output or f"exit code {completed.returncode}"


def parse_tool_arguments(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None
