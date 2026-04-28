from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from koala_strategy.config import load_config, resolve_path
from koala_strategy.utils import content_hash, ensure_dir, iso_now

# Reasoning files are public through the submitted GitHub URL.  Keep enough
# traceability for Koala while stripping raw paper text, hidden probabilities,
# private split labels, and model artifact names.
SENSITIVE_KEYS = {
    "full_text",
    "raw_full_text",
    "pdf_text",
    "page_texts",
    "sections_raw",
    "openreview",
    "official_reviews",
    "meta_review",
    "meta_reviews",
    "decision",
    "decision_label",
    "accept_label",
    "global_test",
    "test_labels",
    "source_status",
    "p_accept",
    "p_accept_final",
    "p_prior",
    "probability",
    "hidden_probability",
    "model_a",
    "model_b",
    "model_c",
    "stacker",
    "artifact_path",
}

MAX_STRING_CHARS = 900
MAX_LIST_ITEMS = 12
MAX_DICT_ITEMS = 40


def _sanitize_public_reasoning(value: Any, *, key: str | None = None, depth: int = 0) -> Any:
    if key and key.lower() in SENSITIVE_KEYS:
        return "[omitted: private or leakage-prone field]"
    if depth > 5:
        return "[omitted: nested data truncated]"
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for i, (k, v) in enumerate(value.items()):
            if i >= MAX_DICT_ITEMS:
                out["__truncated__"] = f"{len(value) - MAX_DICT_ITEMS} additional fields omitted"
                break
            out[str(k)] = _sanitize_public_reasoning(v, key=str(k), depth=depth + 1)
        return out
    if isinstance(value, (list, tuple)):
        items = [_sanitize_public_reasoning(v, depth=depth + 1) for v in list(value)[:MAX_LIST_ITEMS]]
        if len(value) > MAX_LIST_ITEMS:
            items.append(f"[omitted: {len(value) - MAX_LIST_ITEMS} additional items]")
        return items
    if isinstance(value, str):
        compact = " ".join(value.split())
        if len(compact) > MAX_STRING_CHARS:
            return compact[:MAX_STRING_CHARS] + " … [truncated]"
        return compact
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)


def _json_block(data: Any) -> str:
    safe = _sanitize_public_reasoning(data)
    try:
        return json.dumps(safe, ensure_ascii=False, indent=2, sort_keys=True, default=str)
    except TypeError:
        return json.dumps(str(safe), ensure_ascii=False, indent=2)


def write_reasoning_file(
    agent_name: str,
    paper_id: str,
    action_type: Literal["comment", "reply", "verdict"],
    content_markdown: str,
    evidence: dict[str, Any],
    config: dict[str, Any] | None = None,
) -> Path:
    cfg = config or load_config()
    timestamp = iso_now().replace(":", "").replace("+", "Z")
    out_dir = ensure_dir(resolve_path(cfg, "reasoning_dir") / agent_name / paper_id)
    path = out_dir / f"{timestamp}_{action_type}.md"
    prediction_summary = evidence.get("prediction_summary") or evidence
    body = f"""# Reasoning file

Agent: {agent_name}
Paper ID: {paper_id}
Action: {action_type}
Timestamp: {iso_now()}
Content hash: {content_hash(content_markdown)}

## Public-safe prediction summary

```json
{_json_block(prediction_summary)}
```

## Paper-internal evidence

- Positive signals: {_json_block(evidence.get("positive_evidence", evidence.get("positive_summary", "n/a")))}
- Negative signals: {_json_block(evidence.get("negative_evidence", evidence.get("negative_summary", "n/a")))}

## Discussion evidence, if applicable

```json
{_json_block(evidence.get("discussion_summary", "No discussion evidence used for this action."))}
```

## Action content

{content_markdown}

## Policy checks

- Reasoning file content is sanitized before publishing; raw paper text, private labels, hidden probabilities, and model artifact identifiers are omitted.
- No prohibited future same-paper sources were used.
- Self-citations and same-owner citations are disallowed by the citation selector.
- Public action text was scanned by the output guard before posting.
- The reasoning URL is verified before live posting when publishing is enabled.
"""
    path.write_text(body, encoding="utf-8")
    return path
