from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from koala_strategy.config import load_config, project_root
from koala_strategy.llm.providers import get_text_provider
from koala_strategy.schemas import PaperRecord
from koala_strategy.utils import content_hash, dump_json, ensure_dir


PROMPT_VERSION = "comment_polish_v1"


def _cache_path(paper_id: str, draft: str, config: dict[str, Any]) -> Path:
    model = str(config.get("models", {}).get("codex_model", "gpt-5.4-mini"))
    safe_model = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in model)
    cache_root = Path(config.get("models", {}).get("llm_comment_cache_dir") or project_root() / "data" / "llm_comment_cache")
    key = content_hash(PROMPT_VERSION + "\n" + draft)[:16]
    return ensure_dir(cache_root / safe_model) / f"{paper_id}_{key}.json"


def polish_comment_draft(
    paper: PaperRecord,
    draft_markdown: str,
    evidence: dict[str, Any],
    config: dict[str, Any] | None = None,
) -> str:
    cfg = config or load_config()
    models = cfg.get("models", {})
    if not bool(models.get("use_llm_comment_polish", True)):
        return draft_markdown
    if str(models.get("llm_provider", "heuristic")).lower() == "heuristic":
        return draft_markdown
    path = _cache_path(paper.paper_id, draft_markdown, cfg)
    if path.exists():
        try:
            return str(json.loads(path.read_text(encoding="utf-8")).get("polished", draft_markdown))
        except Exception:  # noqa: BLE001
            pass
    prompt = f"""You are polishing a Koala Science peer-review comment.

Rewrite the draft for clarity, concision, and professional review style.
Do not add new technical claims, new citations, new numbers, or outside facts.
Preserve the same factual evidence, score range, uncertainty language, and
overall stance. Keep markdown headings and keep the comment under 430 words.
Do not mention internal model names, hidden labels, training data, or artifacts.

Paper title: {paper.title}

Evidence object:
{evidence}

Draft comment:
{draft_markdown}

Return only the polished markdown comment.
"""
    try:
        polished = get_text_provider(cfg).generate(prompt, model=str(models.get("codex_model", "gpt-5.4-mini")), temperature=0.0).strip()
        if not polished or len(polished.split()) < 60:
            polished = draft_markdown
    except Exception:  # noqa: BLE001
        polished = draft_markdown
    dump_json(path, {"polished": polished, "draft_hash": content_hash(draft_markdown), "prompt_version": PROMPT_VERSION})
    return polished
