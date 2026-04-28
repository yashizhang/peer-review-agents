from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from koala_strategy.config import load_config, project_root, resolve_path
from koala_strategy.llm.json_guard import extract_json_object
from koala_strategy.llm.providers import get_text_provider
from koala_strategy.models.fulltext_evidence_model import parsed_payload_for_paper
from koala_strategy.paper.reviewer_components import component_signal_summary
from koala_strategy.paper.table_evidence import best_table_evidence
from koala_strategy.paper.text_sanitizer import sanitize_model_text, sanitized_sections_text
from koala_strategy.schemas import PaperRecord, PredictionBundle
from koala_strategy.utils import clamp, content_hash, dump_json, ensure_dir


PROMPT_VERSION = "llm_review_judge_v1"


LEAKY_WORDS = [
    "accepted",
    "published",
    "camera-ready",
    "under review",
    "submitted",
    "conference paper",
    "iclr 2026",
    "openreview",
    "paper decision",
    "acknowledg",
    "author contribution",
    "corresponding author",
]


def _clean_line(line: str) -> str:
    return sanitize_model_text(line)


def _trim(text: str, max_chars: int) -> str:
    text = sanitize_model_text(text)
    if len(text) <= max_chars:
        return text
    return text[: int(max_chars * 0.75)] + "\n...\n" + text[-int(max_chars * 0.25) :]


def llm_evidence_payload(paper: PaperRecord, prediction: PredictionBundle) -> dict[str, Any]:
    payload = parsed_payload_for_paper(paper.paper_id) or {}
    tables = []
    for item in best_table_evidence(payload.get("table_evidence") or [], limit=6):
        tables.append(
            {
                "table_id": item.get("table_id"),
                "metrics": item.get("metrics", []),
                "num_numeric_values": item.get("num_numeric_values", 0),
                "context": _trim(str(item.get("caption_or_context", "")), 1200),
            }
        )
    sections = payload.get("sections") or {}
    selected_sections_text = sanitized_sections_text(sections, max_chars_per_section=3200)
    return {
        "paper_id": paper.paper_id,
        "title": paper.title,
        "abstract": _trim(paper.abstract or "", 2400),
        "domains": paper.domains,
        "non_llm_prior": {
            "p_accept": prediction.paper_only.get("p_accept"),
            "recommended_score_range": prediction.paper_only.get("recommended_score_range"),
            "uncertainty": prediction.paper_only.get("uncertainty"),
            "positive_evidence": prediction.paper_only.get("main_positive_evidence", [])[:3],
            "negative_evidence": prediction.paper_only.get("main_negative_evidence", [])[:3],
        },
        "reviewer_component_diagnostics": component_signal_summary_from_payload(paper, prediction),
        "tables": tables,
        "selected_sections": _trim(selected_sections_text, 18000),
        "sanitized_full_text_excerpt": _trim(str(payload.get("full_text") or ""), 22000),
    }


def component_signal_summary_from_payload(paper: PaperRecord, prediction: PredictionBundle) -> list[dict[str, Any]]:
    try:
        # The prediction bundle already stores this when generated after V4.
        rows = prediction.pseudo_review_panel.get("reviewer_component_summary", [])
        if rows:
            return rows
    except Exception:  # noqa: BLE001
        pass
    payload = parsed_payload_for_paper(paper.paper_id)
    if not payload:
        return []
    from koala_strategy.schemas import ParsedPaperText

    return component_signal_summary(ParsedPaperText.model_validate(payload))


def build_llm_judge_prompt(paper: PaperRecord, prediction: PredictionBundle) -> str:
    payload = llm_evidence_payload(paper, prediction)
    return f"""You are the LLM brain inside a Koala Science ICML 2026 review agent.

You must judge this paper using only the public paper evidence below. Do not use
OpenReview reviews, decisions, acceptance status, citation counts, social media,
or later impact. Treat the non-LLM prior as a calibrated statistical prior, not
as ground truth. Adjust it only when the paper evidence supports an adjustment.

Evaluate the paper along these reviewer axes:
1. empirical validation and comparison completeness
2. clarity, presentation, and reproducibility
3. robustness, generalization, scalability, and compute
4. technical soundness and claim support
5. novelty, contribution, and prior-work positioning

Return ONLY one valid JSON object with this exact shape:
{{
  "accept_probability": 0.0,
  "verdict_score": 0.0,
  "confidence": 0.0,
  "axes": {{
    "empirical_validation": {{"score": 0.0, "risk": 0.0, "rationale": "..."}},
    "clarity_reproducibility": {{"score": 0.0, "risk": 0.0, "rationale": "..."}},
    "practical_scope": {{"score": 0.0, "risk": 0.0, "rationale": "..."}},
    "technical_soundness": {{"score": 0.0, "risk": 0.0, "rationale": "..."}},
    "novelty_positioning": {{"score": 0.0, "risk": 0.0, "rationale": "..."}}
  }},
  "strongest_accept_signal": "...",
  "strongest_reject_signal": "...",
  "feedback_actions": ["..."],
  "short_rationale": "..."
}}

Scales:
- accept_probability is 0 to 1.
- verdict_score is 0 to 10.
- confidence is 0 to 1.
- axis score is 0 to 10 where higher is stronger.
- risk is 0 to 1 where higher is more concerning.

Paper evidence JSON:
{json.dumps(payload, ensure_ascii=False, sort_keys=True)}
"""


def _cache_path(paper_id: str, model: str, prompt: str, config: dict[str, Any]) -> Path:
    cache_root = Path(config.get("models", {}).get("llm_judge_cache_dir") or project_root() / "data" / "llm_judge_cache")
    safe_model = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in model)
    key = content_hash(PROMPT_VERSION + "\n" + prompt)[:16]
    return ensure_dir(cache_root / safe_model) / f"{paper_id}_{key}.json"


def _fallback(prediction: PredictionBundle, error: str | None = None) -> dict[str, Any]:
    p = float(prediction.paper_only.get("p_accept", 0.5))
    lo, hi = prediction.paper_only.get("recommended_score_range", [4.5, 5.5])
    return {
        "accept_probability": p,
        "verdict_score": float(lo + hi) / 2.0,
        "confidence": max(0.1, min(0.9, 1.0 - float(prediction.paper_only.get("uncertainty", 0.5)))),
        "axes": {},
        "strongest_accept_signal": (prediction.paper_only.get("main_positive_evidence") or ["No strong accept signal extracted."])[0],
        "strongest_reject_signal": (prediction.paper_only.get("main_negative_evidence") or ["No strong reject signal extracted."])[0],
        "feedback_actions": ["Use the non-LLM evidence prior; LLM judge was unavailable."],
        "short_rationale": f"Fallback to calibrated prior. {error or ''}".strip(),
        "fallback": True,
    }


def validate_llm_judge(data: dict[str, Any], prediction: PredictionBundle) -> dict[str, Any]:
    fallback = _fallback(prediction)
    out = {**fallback, **data, "fallback": False}
    out["accept_probability"] = float(clamp(float(out.get("accept_probability", fallback["accept_probability"])), 0.0, 1.0))
    out["verdict_score"] = float(clamp(float(out.get("verdict_score", fallback["verdict_score"])), 0.0, 10.0))
    out["confidence"] = float(clamp(float(out.get("confidence", fallback["confidence"])), 0.0, 1.0))
    axes = out.get("axes") if isinstance(out.get("axes"), dict) else {}
    cleaned_axes: dict[str, Any] = {}
    for key in ["empirical_validation", "clarity_reproducibility", "practical_scope", "technical_soundness", "novelty_positioning"]:
        row = axes.get(key, {}) if isinstance(axes, dict) else {}
        if not isinstance(row, dict):
            row = {}
        cleaned_axes[key] = {
            "score": float(clamp(float(row.get("score", 5.0)), 0.0, 10.0)),
            "risk": float(clamp(float(row.get("risk", 0.5)), 0.0, 1.0)),
            "rationale": str(row.get("rationale", ""))[:500],
        }
    out["axes"] = cleaned_axes
    out["strongest_accept_signal"] = str(out.get("strongest_accept_signal", fallback["strongest_accept_signal"]))[:800]
    out["strongest_reject_signal"] = str(out.get("strongest_reject_signal", fallback["strongest_reject_signal"]))[:800]
    actions = out.get("feedback_actions", [])
    if not isinstance(actions, list):
        actions = [str(actions)]
    out["feedback_actions"] = [str(x)[:300] for x in actions if str(x).strip()][:5] or fallback["feedback_actions"]
    out["short_rationale"] = str(out.get("short_rationale", fallback["short_rationale"]))[:1000]
    return out


def run_llm_review_judge(
    paper: PaperRecord,
    prediction: PredictionBundle,
    config: dict[str, Any] | None = None,
    force: bool = False,
) -> dict[str, Any]:
    cfg = config or load_config()
    model = str(cfg.get("models", {}).get("codex_model", "gpt-5.4-mini"))
    prompt = build_llm_judge_prompt(paper, prediction)
    path = _cache_path(paper.paper_id, model, prompt, cfg)
    if path.exists() and not force:
        return json.loads(path.read_text(encoding="utf-8"))
    provider = get_text_provider(cfg)
    try:
        raw = provider.generate(prompt, model=model, temperature=0.0)
        data = validate_llm_judge(extract_json_object(raw), prediction)
        data["model"] = model
        data["prompt_version"] = PROMPT_VERSION
        data["cache_path"] = str(path)
        data["raw_response_hash"] = content_hash(raw)
    except Exception as exc:  # noqa: BLE001
        data = _fallback(prediction, error=str(exc)[:300])
        data["model"] = model
        data["prompt_version"] = PROMPT_VERSION
        data["cache_path"] = str(path)
    dump_json(path, data)
    return data


def llm_probability_for_blend(judge: dict[str, Any]) -> float:
    p = float(judge.get("accept_probability", 0.5))
    score = float(judge.get("verdict_score", 5.0)) / 10.0
    confidence = float(clamp(float(judge.get("confidence", 0.5)), 0.0, 1.0))
    # Let the direct probability dominate, but anchor extreme scores slightly.
    return float(clamp((0.75 + 0.15 * confidence) * p + (0.25 - 0.15 * confidence) * score, 0.0, 1.0))


def gated_llm_blend(base_p: float, judge: dict[str, Any]) -> dict[str, float]:
    llm_p = llm_probability_for_blend(judge)
    confidence = float(clamp(float(judge.get("confidence", 0.5)), 0.0, 1.0))
    disagreement = abs(float(llm_p) - float(base_p))
    alpha = 0.40 if confidence >= 0.65 and disagreement >= 0.12 else 0.10
    return {
        "p_base": float(base_p),
        "p_llm": float(llm_p),
        "llm_confidence": confidence,
        "llm_disagreement": float(disagreement),
        "blend_alpha": alpha,
        "p_accept_llm_blend": float(clamp((1.0 - alpha) * float(base_p) + alpha * float(llm_p), 0.0, 1.0)),
    }
