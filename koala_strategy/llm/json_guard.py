from __future__ import annotations

import json
import re
from typing import Any, Callable

from koala_strategy.schemas import PseudoReview


def extract_json_object(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass

    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("No JSON object found")
    snippet = raw[start : end + 1]
    snippet = re.sub(r",\s*([}\]])", r"\1", snippet)
    data = json.loads(snippet)
    if not isinstance(data, dict):
        raise ValueError("Extracted JSON is not an object")
    return data


def validate_pseudo_review(data: dict[str, Any]) -> PseudoReview:
    defaults = {
        "persona": "unknown",
        "novelty": 5,
        "technical_soundness": 5,
        "empirical_rigor": 5,
        "clarity": 5,
        "significance": 5,
        "reproducibility": 5,
        "claim_evidence_alignment": 5,
        "missing_baseline_severity": 2,
        "fatal_flaw_severity": 0,
        "fatal_flaws": [],
        "strongest_accept_signal": "No strong accept signal extracted.",
        "strongest_reject_signal": "No strong reject signal extracted.",
        "confidence": 1,
        "recommended_score_band": "borderline",
        "short_rationale": "Fallback pseudo-review.",
    }
    merged = {**defaults, **data}
    for key in [
        "novelty",
        "technical_soundness",
        "empirical_rigor",
        "clarity",
        "significance",
        "reproducibility",
        "claim_evidence_alignment",
        "confidence",
    ]:
        merged[key] = int(max(1, min(10, int(merged[key]))))
    for key in ["missing_baseline_severity", "fatal_flaw_severity"]:
        merged[key] = int(max(0, min(4, int(merged[key]))))
    return PseudoReview.model_validate(merged)


def fallback_pseudo_review(persona: str = "fallback") -> PseudoReview:
    return validate_pseudo_review(
        {
            "persona": persona,
            "confidence": 1,
            "short_rationale": "parse failed",
            "recommended_score_band": "borderline",
        }
    )


def repair_or_retry_invalid_json(
    call: Callable[[], str],
    persona: str = "unknown",
    retries: int = 2,
) -> PseudoReview:
    last_error: Exception | None = None
    for _ in range(retries + 1):
        try:
            return validate_pseudo_review(extract_json_object(call()))
        except Exception as exc:  # noqa: BLE001
            last_error = exc
    _ = last_error
    return fallback_pseudo_review(persona)

