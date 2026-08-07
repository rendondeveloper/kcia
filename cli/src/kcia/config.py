"""Global and repo-local configuration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml

from kcia.providers.catalog import load_catalog
from kcia.providers.registry import AGENT_ROLES

GLOBAL_CONFIG_DIR = Path.home() / ".config" / "kcia"
GLOBAL_CONFIG_FILE = GLOBAL_CONFIG_DIR / "config.yaml"
USER_DATA_DIR = Path.home() / ".local" / "share" / "kcia"

AgentScope = Literal["global", "repo"]
ConfigOrigin = Literal["flag", "repo", "global", "default"]


@dataclass(frozen=True)
class AgentSetting:
    provider: str
    model: str
    effort: str | None = None


@dataclass(frozen=True)
class ResolvedAgent:
    role: str
    provider: str
    model: str
    effort: str | None
    origin: ConfigOrigin


def repo_local_agents_path(repo_root: Path) -> Path:
    return repo_root / ".ai" / "local" / "agents.yaml"


def load_global_config() -> dict[str, Any]:
    if not GLOBAL_CONFIG_FILE.is_file():
        return {"schema_version": 1, "agents": {}, "preferences": {}}
    data = yaml.safe_load(GLOBAL_CONFIG_FILE.read_text(encoding="utf-8")) or {}
    data.setdefault("schema_version", 1)
    data.setdefault("agents", {})
    data.setdefault("preferences", {})
    return data


def resolve_max_prompt_tokens() -> int:
    """Precedence: user preference > waves.yaml > default."""
    from kcia.waves.definitions import load_budget_config

    prefs = load_global_config().get("preferences", {})
    if "max_prompt_tokens" in prefs:
        return int(prefs["max_prompt_tokens"])
    return load_budget_config().max_prompt_tokens


def save_global_config(config: dict[str, Any]) -> None:
    GLOBAL_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    GLOBAL_CONFIG_FILE.write_text(
        yaml.safe_dump(config, sort_keys=False),
        encoding="utf-8",
    )


def load_repo_agents(repo_root: Path) -> dict[str, Any]:
    path = repo_local_agents_path(repo_root)
    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data.get("agents", {})


def save_repo_agents(repo_root: Path, agents: dict[str, Any]) -> None:
    path = repo_local_agents_path(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump({"schema_version": 1, "agents": agents}, sort_keys=False),
        encoding="utf-8",
    )


def default_agent_setting(role: str) -> AgentSetting:
    catalog = load_catalog()
    if role == "planner":
        provider_id = "claude"
    else:
        provider_id = "cursor"
    if provider_id not in catalog:
        provider_id = next(iter(catalog))
    entry = catalog[provider_id]
    return AgentSetting(provider=provider_id, model=entry.default_model)


def resolve_agents(
    repo_root: Path | None = None,
    *,
    flag_overrides: dict[str, AgentSetting] | None = None,
) -> dict[str, ResolvedAgent]:
    """Resolve planner/builder with §7.1 precedence."""
    catalog = load_catalog()
    global_agents = load_global_config().get("agents", {})
    repo_agents = load_repo_agents(repo_root) if repo_root else {}
    flag_overrides = flag_overrides or {}

    resolved: dict[str, ResolvedAgent] = {}
    for role in AGENT_ROLES:
        if role in flag_overrides:
            setting = flag_overrides[role]
            resolved[role] = ResolvedAgent(
                role=role,
                provider=setting.provider,
                model=setting.model,
                effort=setting.effort,
                origin="flag",
            )
            continue

        if role in repo_agents:
            raw = repo_agents[role]
            resolved[role] = ResolvedAgent(
                role=role,
                provider=raw["provider"],
                model=raw.get("model") or catalog[raw["provider"]].default_model,
                effort=raw.get("effort"),
                origin="repo",
            )
            continue

        if role in global_agents:
            raw = global_agents[role]
            resolved[role] = ResolvedAgent(
                role=role,
                provider=raw["provider"],
                model=raw.get("model") or catalog[raw["provider"]].default_model,
                effort=raw.get("effort"),
                origin="global",
            )
            continue

        default = default_agent_setting(role)
        resolved[role] = ResolvedAgent(
            role=role,
            provider=default.provider,
            model=default.model,
            effort=default.effort,
            origin="default",
        )
    return resolved


def set_agent(
    role: str,
    provider: str,
    *,
    model: str | None = None,
    effort: str | None = None,
    scope: AgentScope = "global",
    repo_root: Path | None = None,
) -> AgentSetting:
    catalog = load_catalog()
    if provider not in catalog:
        available = ", ".join(sorted(catalog))
        raise ValueError(f"unknown provider '{provider}'; available: {available}")

    model_ids = [item.id for item in catalog[provider].models]
    chosen_model = model or catalog[provider].default_model
    if chosen_model not in model_ids:
        raise ValueError(
            f"unknown model '{chosen_model}' for provider '{provider}'; "
            f"available: {', '.join(model_ids)}"
        )

    setting = AgentSetting(provider=provider, model=chosen_model, effort=effort)
    payload = {
        "provider": setting.provider,
        "model": setting.model,
    }
    if setting.effort is not None:
        payload["effort"] = setting.effort

    if scope == "repo":
        if repo_root is None:
            raise ValueError("repo scope requires a repository root")
        agents = load_repo_agents(repo_root)
        agents[role] = payload
        save_repo_agents(repo_root, agents)
    else:
        config = load_global_config()
        config.setdefault("agents", {})
        config["agents"][role] = payload
        save_global_config(config)

    return setting


def swap_agents(
    *,
    scope: AgentScope = "global",
    repo_root: Path | None = None,
) -> None:
    if scope == "repo":
        if repo_root is None:
            raise ValueError("repo scope requires a repository root")
        agents = load_repo_agents(repo_root)
        planner = agents.get("planner")
        builder = agents.get("builder")
        if planner:
            agents["builder"] = planner
        else:
            agents.pop("builder", None)
        if builder:
            agents["planner"] = builder
        else:
            agents.pop("planner", None)
        save_repo_agents(repo_root, agents)
        return

    config = load_global_config()
    agents = config.setdefault("agents", {})
    planner = agents.get("planner")
    builder = agents.get("builder")
    if planner:
        agents["builder"] = planner
    else:
        agents.pop("builder", None)
    if builder:
        agents["planner"] = builder
    else:
        agents.pop("planner", None)
    save_global_config(config)
