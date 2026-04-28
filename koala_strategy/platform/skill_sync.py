from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


from koala_strategy.config import load_config, resolve_path
from koala_strategy.logging_utils import log_event
from koala_strategy.utils import ensure_dir


def _origin_from_api_url(api_url: str) -> str:
    parsed = urlparse(api_url)
    if not parsed.scheme or not parsed.netloc:
        return "https://koala.science"
    return f"{parsed.scheme}://{parsed.netloc}"


def sync_platform_skill_guidance(config: dict[str, Any] | None = None, *, timeout: int = 8) -> dict[str, str]:
    cfg = config or load_config()
    if os.getenv("KOALA_SKIP_SKILL_SYNC", "").strip().lower() in {"1", "true", "yes"}:
        result = {"status": "skipped", "url": str((cfg.get("platform", {}) or {}).get("skill_url") or ""), "reason": "KOALA_SKIP_SKILL_SYNC set"}
        log_event("platform_skill", result, cfg)
        return result
    platform = cfg.get("platform", {})
    explicit_url = str(platform.get("skill_url") or "").strip()
    if explicit_url:
        url = explicit_url
    else:
        base_url = str(platform.get("base_url") or _origin_from_api_url(str(platform.get("api_base_url") or "https://koala.science/api/v1"))).rstrip("/")
        url = f"{base_url}/skill.md"
    try:
        import requests

        response = requests.get(url, timeout=timeout)
        response.raise_for_status()
        text = response.text
        out_dir = ensure_dir(resolve_path(cfg, "logs_dir"))
        path = out_dir / "platform_skill.md"
        path.write_text(text, encoding="utf-8")
        result = {"status": "ok", "url": url, "path": str(path), "bytes": str(len(text.encode('utf-8')))}
    except Exception as exc:  # noqa: BLE001
        result = {"status": "unavailable", "url": url, "error": str(exc)[:500]}
    log_event("platform_skill", result, cfg)
    return result
