from __future__ import annotations

import json
from typing import Any

from koala_strategy.paper.pdf_cache import pdf_cache_paths


def parsed_payload_for_paper(paper_id: str) -> dict[str, Any] | None:
    """Lightweight access to the cached sanitized parsed-paper payload.

    The training module `models.fulltext_evidence_model` also exposes this
    helper, but importing that module pulls in pandas/sklearn at runtime.  Live
    agents need this lightweight version for fast LLM prompts and scheduling.
    """
    _, parsed_path = pdf_cache_paths(paper_id)
    if not parsed_path.exists():
        return None
    try:
        payload = json.loads(parsed_path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    return payload if payload.get("ok") else None
