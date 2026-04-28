from __future__ import annotations

import os
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml


DEFAULT_CONFIG_PATH = Path("strategy_config.yaml")
DEFAULT_LOCAL_CONFIG_PATH = Path("strategy_config.local.yaml")


def _deep_update(base: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_update(base[key], value)
        else:
            base[key] = value
    return base


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    cfg_path = Path(path or os.getenv("KOALA_STRATEGY_CONFIG", DEFAULT_CONFIG_PATH))
    config: dict[str, Any] = {}
    if cfg_path.exists():
        config = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}

    local_cfg_path = os.getenv("KOALA_STRATEGY_LOCAL_CONFIG")
    if local_cfg_path:
        local_path = Path(local_cfg_path)
    elif path is None and not os.getenv("KOALA_STRATEGY_CONFIG"):
        local_path = DEFAULT_LOCAL_CONFIG_PATH
    else:
        local_path = None
    if local_path and local_path.exists():
        _deep_update(config, yaml.safe_load(local_path.read_text(encoding="utf-8")) or {})

    config = deepcopy(config)
    config.setdefault("competition", {})
    config.setdefault("paths", {})
    config.setdefault("models", {})
    config.setdefault("online_policy", {})
    config.setdefault("agents", {})
    config.setdefault("github", {})

    env_overrides: dict[str, Any] = {}
    if "KOALA_DRY_RUN" in os.environ:
        env_overrides.setdefault("online_policy", {})["dry_run"] = os.environ["KOALA_DRY_RUN"].lower() in {
            "1",
            "true",
            "yes",
        }
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

    _deep_update(config, env_overrides)
    return config


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


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
