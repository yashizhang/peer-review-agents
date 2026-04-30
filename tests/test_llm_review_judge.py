from types import SimpleNamespace

import pytest

from koala_strategy.llm.review_judge import validate_llm_judge


def _prediction() -> SimpleNamespace:
    return SimpleNamespace(
        paper_only={
            "p_accept": 0.42,
            "recommended_score_range": [4.0, 5.0],
            "uncertainty": 0.35,
            "main_positive_evidence": ["positive"],
            "main_negative_evidence": ["negative"],
        }
    )


def test_validate_llm_judge_rejects_empty_output() -> None:
    with pytest.raises(ValueError, match="missing required fields"):
        validate_llm_judge({}, _prediction())


def test_validate_llm_judge_accepts_required_fields() -> None:
    judge = validate_llm_judge(
        {
            "accept_probability": 0.61,
            "verdict_score": 6.2,
            "confidence": 0.7,
            "axes": {},
            "strongest_accept_signal": "solid evidence",
            "strongest_reject_signal": "limited concern",
            "feedback_actions": ["focus discussion"],
            "short_rationale": "The evidence leans accept.",
        },
        _prediction(),
    )

    assert judge["fallback"] is False
    assert judge["accept_probability"] == 0.61
    assert judge["verdict_score"] == 6.2
    assert judge["confidence"] == 0.7
