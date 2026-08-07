"""Declarative detection predicate DSL."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

MAX_FILE_BYTES = 256 * 1024


class PredicateError(ValueError):
    """Unknown or malformed predicate."""


_LEAF_PREDICATES = {
    "file_exists",
    "dir_exists",
    "glob_exists",
    "file_absent",
    "dir_absent",
    "yaml_present",
    "yaml_absent",
    "yaml_equals",
    "yaml_any_key",
    "json_present",
    "json_any_key",
    "file_contains",
    "dirs_exist_any",
    "dirs_exist_all",
}

_COMBINATORS = {"all", "any", "not"}


def validate_predicate(predicate: dict[str, Any], *, path: str) -> None:
    if not isinstance(predicate, dict):
        raise PredicateError(f"{path}: predicate must be a mapping")
    if len(predicate) != 1:
        raise PredicateError(f"{path}: predicate must have exactly one key")
    key = next(iter(predicate))
    value = predicate[key]
    if key in _COMBINATORS:
        if key == "not":
            if not isinstance(value, dict):
                raise PredicateError(f"{path}.not: value must be a mapping")
            validate_predicate(value, path=f"{path}.not")
            return
        if not isinstance(value, list) or not value:
            raise PredicateError(f"{path}.{key}: value must be a non-empty list")
        for index, child in enumerate(value):
            validate_predicate(child, path=f"{path}.{key}[{index}]")
        return
    if key not in _LEAF_PREDICATES:
        raise PredicateError(f"{path}: unknown predicate '{key}'")
    _validate_leaf(key, value, path=path)


def _validate_leaf(key: str, value: Any, *, path: str) -> None:
    if key in {"file_exists", "dir_exists", "glob_exists", "file_absent", "dir_absent"}:
        if not isinstance(value, str) or not value:
            raise PredicateError(f"{path}.{key}: expected non-empty string")
        return
    if key in {"yaml_present", "yaml_absent", "json_present"}:
        _expect_mapping(value, path, key, {"file", "path"})
        return
    if key == "yaml_equals":
        _expect_mapping(value, path, key, {"file", "path", "value"})
        return
    if key in {"yaml_any_key", "json_any_key"}:
        _expect_mapping(value, path, key, {"file", "path", "keys"})
        if not isinstance(value["keys"], list) or not value["keys"]:
            raise PredicateError(f"{path}.{key}.keys: expected non-empty list")
        return
    if key == "file_contains":
        _expect_mapping(value, path, key, {"file", "pattern"})
        return
    if key in {"dirs_exist_any", "dirs_exist_all"}:
        if not isinstance(value, list) or not value or not all(isinstance(v, str) for v in value):
            raise PredicateError(f"{path}.{key}: expected non-empty list of strings")


def _expect_mapping(value: Any, path: str, key: str, required: set[str]) -> None:
    if not isinstance(value, dict):
        raise PredicateError(f"{path}.{key}: expected mapping")
    missing = required - set(value)
    if missing:
        raise PredicateError(f"{path}.{key}: missing keys {sorted(missing)}")


class _ReadCache:
    def __init__(self) -> None:
        self._yaml: dict[tuple[str, float], Any] = {}
        self._json: dict[tuple[str, float], Any] = {}
        self._text: dict[tuple[str, float], str] = {}

    def yaml(self, path: Path) -> Any | None:
        key = self._cache_key(path)
        if key in self._yaml:
            return self._yaml[key]
        data = _load_yaml(path)
        self._yaml[key] = data
        return data

    def json(self, path: Path) -> Any | None:
        key = self._cache_key(path)
        if key in self._json:
            return self._json[key]
        data = _load_json(path)
        self._json[key] = data
        return data

    def text(self, path: Path) -> str | None:
        key = self._cache_key(path)
        if key in self._text:
            return self._text[key]
        data = _load_text(path)
        self._text[key] = data
        return data

    @staticmethod
    def _cache_key(path: Path) -> tuple[str, float]:
        stat = path.stat()
        return (str(path), stat.st_mtime)


def evaluate(
    predicate: dict[str, Any],
    root: Path,
    *,
    profile_id: str = "",
    predicate_path: str = "$",
    cache: _ReadCache | None = None,
) -> bool:
    cache = cache or _ReadCache()
    try:
        validate_predicate(predicate, path=predicate_path)
    except PredicateError as exc:
        raise PredicateError(f"profile '{profile_id}': {exc}") from exc
    return _evaluate(predicate, root.resolve(), cache)


def _evaluate(predicate: dict[str, Any], root: Path, cache: _ReadCache) -> bool:
    key, value = next(iter(predicate.items()))
    if key == "all":
        return all(_evaluate(child, root, cache) for child in value)
    if key == "any":
        return any(_evaluate(child, root, cache) for child in value)
    if key == "not":
        return not _evaluate(value, root, cache)
    return _evaluate_leaf(key, value, root, cache)


def _evaluate_leaf(key: str, value: Any, root: Path, cache: _ReadCache) -> bool:
    if key == "file_exists":
        return _safe_path(root, value).is_file()
    if key == "dir_exists":
        return _safe_path(root, value).is_dir()
    if key == "glob_exists":
        return any(root.glob(value))
    if key == "file_absent":
        return not _safe_path(root, value).exists()
    if key == "dir_absent":
        path = _safe_path(root, value)
        return not path.exists() or not path.is_dir()
    if key == "yaml_present":
        return _yaml_path(cache, root, value["file"], value["path"]) is not None
    if key == "yaml_absent":
        return _yaml_path(cache, root, value["file"], value["path"]) is None
    if key == "yaml_equals":
        actual = _yaml_path(cache, root, value["file"], value["path"], missing_ok=True)
        return actual == value["value"]
    if key == "yaml_any_key":
        mapping = _yaml_path(cache, root, value["file"], value["path"])
        if not isinstance(mapping, dict):
            return False
        return any(item in mapping for item in value["keys"])
    if key == "json_present":
        return _json_path(cache, root, value["file"], value["path"]) is not None
    if key == "json_any_key":
        mapping = _json_path(cache, root, value["file"], value["path"])
        if not isinstance(mapping, dict):
            return False
        return any(item in mapping for item in value["keys"])
    if key == "file_contains":
        text = cache.text(_safe_path(root, value["file"]))
        if text is None:
            return False
        return re.search(value["pattern"], text) is not None
    if key == "dirs_exist_any":
        return any(_safe_path(root, item).is_dir() for item in value)
    if key == "dirs_exist_all":
        return all(_safe_path(root, item).is_dir() for item in value)
    return False


def _safe_path(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    root_resolved = root.resolve()
    if root_resolved not in candidate.parents and candidate != root_resolved:
        return root / "__outside_root__"
    return candidate


def _load_yaml(path: Path) -> Any | None:
    if not path.is_file():
        return None
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        logger.warning("predicate yaml read failed for %s: %s", path, exc)
        return None


def _load_json(path: Path) -> Any | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("predicate json read failed for %s: %s", path, exc)
        return None


def _load_text(path: Path) -> str | None:
    if not path.is_file():
        return None
    try:
        data = path.read_bytes()[:MAX_FILE_BYTES]
        return data.decode("utf-8", errors="replace")
    except OSError as exc:
        logger.warning("predicate text read failed for %s: %s", path, exc)
        return None


def _yaml_path(
    cache: _ReadCache,
    root: Path,
    file_name: str,
    dotted_path: str,
    *,
    missing_ok: bool = False,
) -> Any | None:
    data = cache.yaml(_safe_path(root, file_name))
    if data is None:
        return None if missing_ok else None
    current: Any = data
    for part in dotted_path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _json_path(cache: _ReadCache, root: Path, file_name: str, dotted_path: str) -> Any | None:
    data = cache.json(_safe_path(root, file_name))
    if data is None:
        return None
    current: Any = data
    for part in dotted_path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current
