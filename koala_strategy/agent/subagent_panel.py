from __future__ import annotations

from typing import Any

from koala_strategy.schemas import PaperRecord, PredictionBundle


INTERNAL_SUBAGENTS = [
    "evidence_mapper",
    "rigor_auditor",
    "novelty_scout",
    "calibration_chair",
]


def plan_review_operation(paper: PaperRecord, prediction: PredictionBundle) -> dict[str, Any]:
    fulltext = prediction.paper_only.get("fulltext_evidence", {})
    positives = prediction.paper_only.get("main_positive_evidence", [])
    negatives = prediction.paper_only.get("main_negative_evidence", [])
    return {
        "lead_agent": "review_director",
        "internal_subagents": {
            "evidence_mapper": {
                "task": "extract page/table/section evidence that can support a citable Koala comment",
                "focus_evidence": positives[:3],
                "fulltext_available": bool(fulltext.get("available")),
            },
            "rigor_auditor": {
                "task": "check baselines, ablations, metrics, variance, and reproducibility",
                "risk_evidence": negatives[:3],
            },
            "novelty_scout": {
                "task": "check whether the contribution is genuinely new relative to closest prior work",
                "domain": prediction.domain,
            },
            "calibration_chair": {
                "task": "convert evidence into a calibrated verdict prior and score range",
                "p_accept": prediction.paper_only.get("p_accept"),
                "score_range": prediction.paper_only.get("recommended_score_range"),
                "uncertainty": prediction.paper_only.get("uncertainty"),
            },
        },
    }

