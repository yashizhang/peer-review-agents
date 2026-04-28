from __future__ import annotations

from typing import Any
import os
import shutil

from koala_strategy.agent.github_publisher import build_github_file_url, reasoning_branch_name
from koala_strategy.config import effective_config_summary, load_config, validate_runtime_config
from koala_strategy.logging_utils import log_event
from koala_strategy.platform.koala_client import KoalaClient
from koala_strategy.platform.skill_sync import sync_platform_skill_guidance
from koala_strategy.llm.agentic_reviewer import agentic_llm_enabled


def run_preflight(agent_name: str = "review_director", dry_run: bool | None = None, config: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = config or load_config()
    effective_dry_run = bool(cfg.get("online_policy", {}).get("dry_run", True) if dry_run is None else dry_run)
    validate_runtime_config(cfg, agent_name, dry_run=effective_dry_run)
    summary = effective_config_summary(cfg, agent_name, dry_run=effective_dry_run)
    skill = sync_platform_skill_guidance(cfg)
    client = KoalaClient(agent_name, dry_run=effective_dry_run, config=cfg)
    profile: dict[str, Any] = {"dry_run": True}
    papers_seen = 0
    if not effective_dry_run:
        profile = client.get_agent_profile()
        papers_seen = len(client.list_papers(status="in_review")[:5])
    github = cfg.get("github", {})
    sample_url = None
    if github.get("repo"):
        sample_url = build_github_file_url(
            github["repo"],
            reasoning_branch_name(agent_name, "sample-paper"),
            "reasoning/sample.md",
            url_style=str(github.get("url_style") or "blob"),
            raw_base_url=github.get("raw_base_url"),
        )
    provider = str(cfg.get("models", {}).get("llm_provider", "heuristic")).lower()
    if provider == "codex":
        llm_ready = shutil.which("codex") is not None
        llm_ready_reason = "codex CLI found" if llm_ready else "codex CLI not found on PATH"
    elif provider == "api":
        llm_ready = bool(os.getenv("OPENAI_API_KEY"))
        llm_ready_reason = "OPENAI_API_KEY set" if llm_ready else "OPENAI_API_KEY not set"
    else:
        llm_ready = False
        llm_ready_reason = f"llm_provider={provider}"
    result = {
        "agent_name": agent_name,
        "dry_run": effective_dry_run,
        "effective_config": summary,
        "skill_sync": skill,
        "profile_keys": sorted(profile.keys()),
        "sample_github_file_url": sample_url,
        "in_review_probe_count": papers_seen,
        "agentic_llm_enabled": agentic_llm_enabled(cfg),
        "llm_provider_ready": llm_ready,
        "llm_provider_ready_reason": llm_ready_reason,
        "ok": True,
    }
    log_event("preflight", result, cfg)
    return result
