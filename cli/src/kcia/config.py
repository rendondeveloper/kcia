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
    billing_profile: str | None = None


@dataclass(frozen=True)
class ResolvedAgent:
    role: str
    provider: str
    model: str
    effort: str | None
    origin: ConfigOrigin
    fallbacks: tuple[AgentSetting, ...] = ()
    automatic: bool = False
    active_index: int = 0
    manual_pin: bool = False

    @property
    def primary(self) -> AgentSetting:
        return AgentSetting(
            provider=self.provider,
            model=self.model,
            effort=self.effort,
        )


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
        yaml.safe_dump({"schema_version": 2, "agents": agents}, sort_keys=False),
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


def default_route_policy() -> dict[str, Any]:
    return {
        "automatic": False,
        "warn_at_percent": 90,
        "proactive_switch_at_percent": None,
        "return_to_primary": "safe_boundary",
        "all_unavailable": "wait",
        "max_switches_per_operation": 3,
        "allow_paid_overage": False,
        "quota_poll_seconds": 60,
        "unknown_quota_retry_seconds": 300,
        "anti_flap_seconds": 120,
    }


def resolve_agents(
    repo_root: Path | None = None,
    *,
    flag_overrides: dict[str, AgentSetting] | None = None,
) -> dict[str, ResolvedAgent]:
    """Resolve planner/builder with §7.1 precedence."""
    catalog = load_catalog()
    global_config = load_global_config()
    global_agents = global_config.get("agents", {})
    global_routing = global_config.get("routing", {})
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
            resolved[role] = _resolved_agent_from_raw(
                role=role,
                raw=raw,
                origin="repo",
                routing={},
                catalog=catalog,
            )
            continue

        if role in global_agents:
            raw = global_agents[role]
            resolved[role] = _resolved_agent_from_raw(
                role=role,
                raw=raw,
                origin="global",
                routing=global_routing,
                catalog=catalog,
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


def _resolved_agent_from_raw(
    *,
    role: str,
    raw: dict[str, Any],
    origin: ConfigOrigin,
    routing: dict[str, Any],
    catalog: dict[str, Any],
) -> ResolvedAgent:
    primary = _primary_from_raw(raw, catalog)
    return ResolvedAgent(
        role=role,
        provider=primary.provider,
        model=primary.model,
        effort=primary.effort,
        origin=origin,
        fallbacks=tuple(_settings_from_raw(raw.get("fallbacks") or [], catalog)),
        automatic=bool(raw.get("automatic", routing.get("automatic", False))),
        active_index=int(raw.get("active_index") or 0),
        manual_pin=bool(raw.get("manual_pin", False)),
    )


def _primary_from_raw(raw: dict[str, Any], catalog: dict[str, Any]) -> AgentSetting:
    if isinstance(raw.get("primary"), dict):
        return _setting_from_raw(raw["primary"], catalog)
    return _setting_from_raw(raw, catalog)


def _settings_from_raw(
    raw_items: list[dict[str, Any]],
    catalog: dict[str, Any],
) -> list[AgentSetting]:
    return [_setting_from_raw(item, catalog) for item in raw_items]


def _setting_from_raw(raw: dict[str, Any], catalog: dict[str, Any]) -> AgentSetting:
    provider = raw["provider"]
    return AgentSetting(
        provider=provider,
        model=raw.get("model") or catalog[provider].default_model,
        effort=raw.get("effort"),
        billing_profile=raw.get("billing_profile"),
    )


def model_in_catalog(provider: str, model: str) -> bool:
    """Whether the provider still offers this model id.

    Stored configuration outlives the catalog: a model that is renamed upstream
    stays in config.yaml and silently reaches the provider as an invalid id.

    Providers with ``model_source: live`` accept any non-empty model id; the
    catalog lists documented defaults, not a hard allowlist.
    """
    catalog = load_catalog()
    entry = catalog.get(provider)
    if entry is None:
        return False
    if entry.model_source == "live":
        return bool(model)
    return any(item.id == model for item in entry.models)


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

    entry = catalog[provider]
    model_ids = [item.id for item in entry.models]
    chosen_model = model or entry.default_model
    if entry.model_source != "live" and chosen_model not in model_ids:
        raise ValueError(
            f"unknown model '{chosen_model}' for provider '{provider}'; "
            f"available: {', '.join(model_ids)}"
        )
    if not chosen_model:
        raise ValueError(f"no model specified for provider '{provider}'")

    setting = AgentSetting(provider=provider, model=chosen_model, effort=effort)
    payload = _agent_payload(setting)

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


def add_agent_fallback(
    role: str,
    provider: str,
    *,
    model: str | None = None,
    effort: str | None = None,
    billing_profile: str | None = None,
    scope: AgentScope = "global",
    repo_root: Path | None = None,
) -> AgentSetting:
    catalog = load_catalog()
    _validate_model(catalog, provider, model)
    entry = catalog[provider]
    setting = AgentSetting(
        provider=provider,
        model=model or entry.default_model,
        effort=effort,
        billing_profile=billing_profile
        or _catalog_billing_profile(entry.models, model or entry.default_model),
    )
    agents = _load_agents_for_scope(scope, repo_root)
    route = _normalize_agent_route(agents.get(role), role)
    fallbacks = route.setdefault("fallbacks", [])
    if any(
        item.get("provider") == setting.provider and item.get("model") == setting.model
        for item in fallbacks
    ):
        raise ValueError(
            f"fallback already exists for {role}: {provider}/{setting.model}"
        )
    if (
        route["primary"]["provider"] == setting.provider
        and route["primary"].get("model") == setting.model
    ):
        raise ValueError(
            f"fallback duplicates primary for {role}: {provider}/{setting.model}"
        )
    fallbacks.append(_agent_payload(setting))
    agents[role] = route
    _save_agents_for_scope(scope, repo_root, agents)
    return setting


def set_agent_active_target(
    role: str,
    *,
    active_index: int,
    manual_pin: bool | None = None,
    scope: AgentScope = "global",
    repo_root: Path | None = None,
) -> None:
    agents = _load_agents_for_scope(scope, repo_root)
    route = _normalize_agent_route(agents.get(role), role)
    _validate_active_index(route, active_index)
    route["active_index"] = active_index
    if manual_pin is not None:
        route["manual_pin"] = manual_pin
    agents[role] = route
    _save_agents_for_scope(scope, repo_root, agents)


def pin_agent(
    role: str,
    *,
    scope: AgentScope = "global",
    repo_root: Path | None = None,
) -> None:
    agents = _load_agents_for_scope(scope, repo_root)
    route = _normalize_agent_route(agents.get(role), role)
    active_index = int(route.get("active_index") or 0)
    _validate_active_index(route, active_index)
    route["manual_pin"] = True
    agents[role] = route
    _save_agents_for_scope(scope, repo_root, agents)


def unpin_agent(
    role: str,
    *,
    scope: AgentScope = "global",
    repo_root: Path | None = None,
) -> None:
    agents = _load_agents_for_scope(scope, repo_root)
    route = _normalize_agent_route(agents.get(role), role)
    route["manual_pin"] = False
    agents[role] = route
    _save_agents_for_scope(scope, repo_root, agents)


def swap_agents(
    *,
    scope: AgentScope = "global",
    repo_root: Path | None = None,
) -> None:
    agents = _load_agents_for_scope(scope, repo_root)
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
    _save_agents_for_scope(scope, repo_root, agents)


def _agent_payload(setting: AgentSetting) -> dict[str, Any]:
    payload = {
        "provider": setting.provider,
        "model": setting.model,
    }
    if setting.effort is not None:
        payload["effort"] = setting.effort
    if setting.billing_profile is not None:
        payload["billing_profile"] = setting.billing_profile
    return payload


def _normalize_agent_route(raw: dict[str, Any] | None, role: str) -> dict[str, Any]:
    if raw is None:
        default = default_agent_setting(role)
        return {
            "primary": _agent_payload(default),
            "fallbacks": [],
            "automatic": False,
            "active_index": 0,
            "manual_pin": False,
        }
    if "primary" in raw:
        normalized = dict(raw)
        normalized.setdefault("fallbacks", [])
        normalized.setdefault("active_index", 0)
        normalized.setdefault("manual_pin", False)
        return normalized
    return {
        "primary": {key: value for key, value in raw.items() if key != "fallbacks"},
        "fallbacks": list(raw.get("fallbacks") or []),
        "automatic": False,
        "active_index": 0,
        "manual_pin": False,
    }


def _load_agents_for_scope(scope: AgentScope, repo_root: Path | None) -> dict[str, Any]:
    if scope == "repo":
        if repo_root is None:
            raise ValueError("repo scope requires a repository root")
        return load_repo_agents(repo_root)
    return load_global_config().setdefault("agents", {})


def _save_agents_for_scope(
    scope: AgentScope,
    repo_root: Path | None,
    agents: dict[str, Any],
) -> None:
    if scope == "repo":
        if repo_root is None:
            raise ValueError("repo scope requires a repository root")
        save_repo_agents(repo_root, agents)
        return
    config = load_global_config()
    config["schema_version"] = max(int(config.get("schema_version", 1)), 2)
    config["agents"] = agents
    config.setdefault("routing", default_route_policy())
    save_global_config(config)


def _validate_model(
    catalog: dict[str, Any],
    provider: str,
    model: str | None,
) -> None:
    if provider not in catalog:
        available = ", ".join(sorted(catalog))
        raise ValueError(f"unknown provider '{provider}'; available: {available}")
    entry = catalog[provider]
    chosen_model = model or entry.default_model
    model_ids = [item.id for item in entry.models]
    if entry.model_source != "live" and chosen_model not in model_ids:
        raise ValueError(
            f"unknown model '{chosen_model}' for provider '{provider}'; "
            f"available: {', '.join(model_ids)}"
        )
    if not chosen_model:
        raise ValueError(f"no model specified for provider '{provider}'")


def _catalog_billing_profile(models: list[Any], model_id: str) -> str | None:
    for model in models:
        if model.id == model_id:
            return model.billing_profile
    return None


def _validate_active_index(route: dict[str, Any], active_index: int) -> None:
    max_index = len(route.get("fallbacks") or [])
    if active_index < 0 or active_index > max_index:
        raise ValueError(
            f"active index {active_index} is out of range; "
            f"available range is 0..{max_index}"
        )
