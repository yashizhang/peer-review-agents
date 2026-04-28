from __future__ import annotations

import re
import subprocess
import time
from pathlib import Path
from typing import Any


from koala_strategy.config import load_config, project_root


class _RequestsProxy:
    def get(self, *args, **kwargs):
        import requests as _requests

        return _requests.get(*args, **kwargs)


requests = _RequestsProxy()

_GITHUB_URL_RE = re.compile(r"^https://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$")
_GITHUB_SSH_RE = re.compile(r"^git@github\.com:([^/]+)/(.+?)(?:\.git)?$")
_OWNER_REPO_RE = re.compile(r"^([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)$")


def reasoning_branch_name(agent_name: str, paper_id: str) -> str:
    safe_agent = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in agent_name)
    safe_paper = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in paper_id)
    return f"agent-reasoning/{safe_agent}/{safe_paper}"


def parse_github_repo(repo: str) -> tuple[str, str]:
    repo = str(repo or "").strip().removesuffix("/")
    for regex in (_GITHUB_URL_RE, _GITHUB_SSH_RE, _OWNER_REPO_RE):
        match = regex.match(repo)
        if match:
            owner, name = match.group(1), match.group(2).removesuffix(".git")
            return owner, name
    raise ValueError(
        "github.repo must be one of: https://github.com/OWNER/REPO, "
        "git@github.com:OWNER/REPO.git, or OWNER/REPO"
    )


def is_allowed_github_file_url(url: str) -> bool:
    return url.startswith("https://github.com/") or url.startswith("https://raw.githubusercontent.com/")


def verify_github_url(url: str, timeout: int = 10, retries: int = 1, sleep_seconds: float = 1.0) -> bool:
    if not is_allowed_github_file_url(url):
        return False
    for attempt in range(max(1, retries)):
        try:
            response = requests.get(url, timeout=timeout)
            if response.status_code == 200:
                return True
        except Exception:
            pass
        if attempt + 1 < max(1, retries):
            time.sleep(sleep_seconds)
    return False


def _relative_repo_path(file_path: Path) -> str:
    path = Path(file_path).resolve()
    root = project_root().resolve()
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        cwd = Path.cwd().resolve()
        try:
            return path.relative_to(cwd).as_posix()
        except ValueError:
            return path.as_posix().lstrip("/")


def build_github_file_url(repo: str, branch: str, rel_path: str, *, url_style: str = "blob", raw_base_url: str | None = None) -> str:
    branch = branch.strip("/")
    rel_path = rel_path.strip("/")
    if url_style == "raw" and raw_base_url:
        return f"{raw_base_url.rstrip('/')}/{branch}/{rel_path}"
    owner, name = parse_github_repo(repo)
    if url_style == "raw":
        return f"https://raw.githubusercontent.com/{owner}/{name}/{branch}/{rel_path}"
    return f"https://github.com/{owner}/{name}/blob/{branch}/{rel_path}"


def _git(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], check=True, text=True, capture_output=True)


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
    rel_path = _relative_repo_path(file_path)
    if not publish_enabled:
        return f"dry-run://{branch}/{rel_path}"

    repo = str(github.get("repo") or "")
    url_style = str(github.get("url_style") or "blob").lower()
    if url_style not in {"blob", "raw"}:
        url_style = "blob"
    url = build_github_file_url(repo, branch, rel_path, url_style=url_style, raw_base_url=github.get("raw_base_url"))

    _git(["rev-parse", "--is-inside-work-tree"])
    _git(["checkout", "-B", branch])
    _git(["add", rel_path])
    status = _git(["status", "--porcelain", "--", rel_path]).stdout.strip()
    if status:
        _git(["commit", "-m", f"Add reasoning for {agent_name} {paper_id}", "--", rel_path])
    _git(["push", "-u", "origin", branch, "--force-with-lease"])

    if bool(github.get("verify_after_publish", True)):
        retries = int(cfg.get("online_policy", {}).get("verify_github_url_retries", 3))
        if not verify_github_url(url, retries=retries):
            raise RuntimeError(f"published reasoning URL did not verify with HTTP 200: {url}")
    return url
