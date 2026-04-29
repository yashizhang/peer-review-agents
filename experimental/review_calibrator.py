#!/usr/bin/env python3
"""Helpers for bounded separate calibration."""

from __future__ import annotations

DECISION_BANDS = [
    "clear reject",
    "weak reject",
    "weak accept",
    "strong accept",
    "spotlight",
]
CONFIDENCE_LEVELS = {"low", "medium", "high"}


def band_index(decision_band: str) -> int:
    return DECISION_BANDS.index(decision_band)


def clamp_band_step(base_band: str, target_band: str, max_steps: int = 1) -> str:
    base_index = band_index(base_band)
    target_index = band_index(target_band)
    if target_index > base_index + max_steps:
        target_index = base_index + max_steps
    if target_index < base_index - max_steps:
        target_index = base_index - max_steps
    return DECISION_BANDS[target_index]


def score_to_band(score: float) -> str:
    if score < 3.0:
        return "clear reject"
    if score < 5.0:
        return "weak reject"
    if score < 7.0:
        return "weak accept"
    if score < 9.0:
        return "strong accept"
    return "spotlight"


def select_category_priors(priors: dict, categories: list[str]) -> dict:
    selected = {}
    for category in categories:
        if category in priors.get("categories", {}):
            selected[category] = priors["categories"][category]
    if not selected and priors.get("categories"):
        for category in sorted(priors["categories"])[:2]:
            selected[category] = priors["categories"][category]
    return {
        "artifact_type": priors.get("artifact_type"),
        "excluded_eval_forum_count": priors.get("excluded_eval_forum_count"),
        "categories": selected,
    }


def apply_calibration(base_result: dict, calibration: dict) -> dict:
    base_score = float(base_result["score"])
    base_band = base_result["decision_band"]
    requested_delta = float(calibration.get("delta_score", 0.0))
    applied_delta = max(-0.5, min(0.5, requested_delta))
    final_score = min(10.0, max(0.0, base_score + applied_delta))

    suggested_band = calibration.get("suggested_decision_band", base_band)
    if suggested_band not in DECISION_BANDS:
        suggested_band = base_band
    clamped_suggested_band = clamp_band_step(base_band, suggested_band, max_steps=1)
    score_band = clamp_band_step(base_band, score_to_band(final_score), max_steps=1)
    final_band = clamped_suggested_band if clamped_suggested_band == score_band else score_band

    confidence = calibration.get("confidence", base_result.get("confidence", "medium"))
    if confidence not in CONFIDENCE_LEVELS:
        confidence = base_result.get("confidence", "medium")

    return {
        "score": round(final_score, 4),
        "decision_band": final_band,
        "confidence": confidence,
        "delta_score_requested": requested_delta,
        "delta_score_applied": round(applied_delta, 4),
        "suggested_decision_band": suggested_band,
        "applied_decision_band": final_band,
        "band_changed": final_band != base_band,
        "calibration_note": calibration.get("calibration_note", ""),
        "memory_used": bool(calibration.get("use_priors")),
    }
