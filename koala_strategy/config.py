from __future__ import annotations

import os
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

try:  # Python 3.11+
    import tomllib
except Exception:  # pragma: no cover
    tomllib = None  # type: ignore[assignment]


DEFAULT_CONFIG_PATH = Path("strategy_config.yaml")
DEFAULT_LOCAL_CONFIG_PATH = Path("strategy_config.local.yaml")


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _deep_update(base: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_update(base[key], value)
        else:
            base[key] = value
    return base


def _parse_bool(raw: str | bool | None, default: bool = False) -> bool:
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}


def _candidate_config_path(path: str | Path | None) -> Path:
    requested = Path(path or os.getenv("KOALA_STRATEGY_CONFIG", DEFAULT_CONFIG_PATH))
    if requested.is_absolute() or requested.exists():
        return requested
    rooted = project_root() / requested
    return rooted if rooted.exists() else requested


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _read_root_toml_bridge() -> dict[str, Any]:
    """Bridge the old root config.toml into the canonical strategy config.

    The project used to have two competing config sources.  YAML remains the
    source of truth, but selected root TOML fields are used only when YAML/env do
    not provide them.
    """
    if tomllib is None:
        return {}
    path = project_root() / "config.toml"
    if not path.exists():
        return {}
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    bridge: dict[str, Any] = {}
    if "dry_run" in data:
        bridge.setdefault("online_policy", {})["dry_run"] = bool(data["dry_run"])
    if data.get("github_repo"):
        bridge.setdefault("github", {})["repo"] = data["github_repo"]
    if data.get("github_raw_base_url"):
        bridge.setdefault("github", {})["raw_base_url"] = data["github_raw_base_url"]
    if data.get("koala_base_url"):
        bridge.setdefault("platform", {})["base_url"] = data["koala_base_url"]
    if data.get("koala_api_base_url"):
        bridge.setdefault("platform", {})["api_base_url"] = data["koala_api_base_url"]
    return bridge


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    cfg_path = _candidate_config_path(path)
    config: dict[str, Any] = _read_yaml(cfg_path)

    # Canonical defaults first, then bridge legacy TOML only into missing fields,
    # then local/env overrides.
    config = deepcopy(config)
    for key in ("competition", "paths", "models", "online_policy", "agents", "github", "platform"):
        config.setdefault(key, {})
    bridge = _read_root_toml_bridge()
    for section, values in bridge.items():
        if isinstance(values, dict):
            for key, value in values.items():
                config.setdefault(section, {})
                if config[section].get(key) in (None, "", []):
                    config[section][key] = value

    local_cfg_path = os.getenv("KOALA_STRATEGY_LOCAL_CONFIG")
    if local_cfg_path:
        local_path = Path(local_cfg_path)
    elif path is None and not os.getenv("KOALA_STRATEGY_CONFIG"):
        local_path = project_root() / DEFAULT_LOCAL_CONFIG_PATH
    else:
        local_path = None
    if local_path and local_path.exists():
        _deep_update(config, _read_yaml(local_path))

    env_overrides: dict[str, Any] = {}
    if "KOALA_DRY_RUN" in os.environ:
        env_overrides.setdefault("online_policy", {})["dry_run"] = _parse_bool(os.environ.get("KOALA_DRY_RUN"))
    if os.getenv("KOALA_MODEL_VERSION"):
        env_overrides.setdefault("models", {})["version"] = os.environ["KOALA_MODEL_VERSION"]
    if os.getenv("KOALA_LLM_PROVIDER"):
        env_overrides.setdefault("models", {})["llm_provider"] = os.environ["KOALA_LLM_PROVIDER"]
    if os.getenv("KOALA_CODEX_MODEL"):
        env_overrides.setdefault("models", {})["codex_model"] = os.environ["KOALA_CODEX_MODEL"]
    if os.getenv("KOALA_CODEX_AUTH_FILE"):
        env_overrides.setdefault("models", {})["codex_auth_file"] = os.environ["KOALA_CODEX_AUTH_FILE"]
    if os.getenv("KOALA_GITHUB_REPO"):
        env_overrides.setdefault("github", {})["repo"] = os.environ["KOALA_GITHUB_REPO"]
    if os.getenv("KOALA_GITHUB_RAW_BASE_URL"):
        env_overrides.setdefault("github", {})["raw_base_url"] = os.environ["KOALA_GITHUB_RAW_BASE_URL"]
    if "KOALA_GITHUB_PUBLISH_ENABLED" in os.environ:
        env_overrides.setdefault("github", {})["publish_enabled"] = _parse_bool(os.environ.get("KOALA_GITHUB_PUBLISH_ENABLED"))
    if os.getenv("KOALA_BASE_URL"):
        env_overrides.setdefault("platform", {})["base_url"] = os.environ["KOALA_BASE_URL"]
    if os.getenv("KOALA_API_BASE_URL"):
        env_overrides.setdefault("platform", {})["api_base_url"] = os.environ["KOALA_API_BASE_URL"]
    if os.getenv("KOALA_AUTH_SCHEME"):
        env_overrides.setdefault("platform", {})["auth_scheme"] = os.environ["KOALA_AUTH_SCHEME"]
    if os.getenv("KOALA_SAME_OWNER_AGENTS"):
        agents = [x.strip() for x in os.environ["KOALA_SAME_OWNER_AGENTS"].split(",") if x.strip()]
        env_overrides.setdefault("competition", {})["same_owner_agent_names"] = agents

    _deep_update(config, env_overrides)
    return config


def resolve_path(config: dict[str, Any], key: str) -> Path:
    path = Path(config["paths"][key])
    if path.is_absolute():
        return path
    return project_root() / path


def get_agent_config(config: dict[str, Any], agent_name: str) -> dict[str, Any]:
    agents = config.get("agents", {})
    if agent_name not in agents:
        raise KeyError(f"Unknown agent: {agent_name}")
    return agents[agent_name]


def effective_config_summary(config: dict[str, Any], agent_name: str, *, dry_run: bool | None = None) -> dict[str, Any]:
    policy = config.get("online_policy", {})
    github = config.get("github", {})
    platform = config.get("platform", {})
    return {
        "agent_name": agent_name,
        "dry_run": bool(policy.get("dry_run", True) if dry_run is None else dry_run),
        "publish_enabled": bool(github.get("publish_enabled", False)),
        "github_repo": github.get("repo") or "",
        "github_raw_base_url": github.get("raw_base_url") or "",
        "platform_base_url": platform.get("base_url") or "https://koala.science",
        "platform_api_base_url": platform.get("api_base_url") or os.getenv("KOALA_API_BASE_URL") or "https://koala.science/api/v1",
        "model_version": config.get("models", {}).get("version"),
        "llm_provider": config.get("models", {}).get("llm_provider"),
        "max_first_comments_per_day": policy.get("max_first_comments_per_agent_per_day"),
        "max_comments_per_paper_by_agent": policy.get("max_comments_per_paper_by_agent"),
    }


def validate_runtime_config(config: dict[str, Any], agent_name: str, *, dry_run: bool) -> None:
    if agent_name not in config.get("agents", {}):
        raise ValueError(f"Unknown agent '{agent_name}'. Available: {sorted(config.get('agents', {}))}")
    github = config.get("github", {})
    if not dry_run:
        if not github.get("publish_enabled"):
            raise ValueError("Real posting requires github.publish_enabled=true or KOALA_GITHUB_PUBLISH_ENABLED=1.")
        if not github.get("repo"):
            raise ValueError("Real posting requires github.repo or KOALA_GITHUB_REPO.")
