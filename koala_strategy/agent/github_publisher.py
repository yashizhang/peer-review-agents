from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import requests

from koala_strategy.config import load_config


def reasoning_branch_name(agent_name: str, paper_id: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in paper_id)
    return f"agent-reasoning/{agent_name}/{safe}"


def verify_github_url(url: str, timeout: int = 10) -> bool:
    if not url.startswith(("http://", "https://")):
        return False
    try:
        response = requests.get(url, timeout=timeout)
        return response.status_code == 200
    except requests.RequestException:
        return False


def publish_reasoning_file(
    agent_name: str,
    paper_id: str,
    file_path: Path,
    config: dict[str, Any] | None = None,
) -> str:
    cfg = config or load_config()
    github = cfg.get("github", {})
    publish_enabled = bool(github.get("publish_enabled", False))
    branch = reasoning_branch_name(agent_name, paper_id)
    raw_base_url = str(github.get("raw_base_url") or "").rstrip("/")
    rel_path = file_path.as_posix()
    if rel_path.startswith(str(Path.cwd())):
        rel_path = str(file_path.relative_to(Path.cwd()))
    if not publish_enabled:
        return f"dry-run://{branch}/{rel_path}"

    subprocess.run(["git", "checkout", "-B", branch], check=True)
    subprocess.run(["git", "add", str(file_path)], check=True)
    subprocess.run(["git", "commit", "-m", f"Add reasoning for {agent_name} {paper_id}"], check=True)
    subprocess.run(["git", "push", "-u", "origin", branch, "--force-with-lease"], check=True)
    if raw_base_url:
        return f"{raw_base_url}/{branch}/{rel_path}"
    repo = str(github.get("repo") or "").removesuffix(".git")
    if repo.startswith("https://github.com/"):
        return repo.replace("https://github.com/", "https://raw.githubusercontent.com/") + f"/{branch}/{rel_path}"
    return f"https://raw.githubusercontent.com/{repo}/{branch}/{rel_path}"

