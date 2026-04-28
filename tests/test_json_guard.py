import pytest

from koala_strategy.llm.json_guard import extract_json_object, fallback_pseudo_review, repair_or_retry_invalid_json, validate_pseudo_review


def test_parses_valid_json():
    data = extract_json_object('{"novelty": 7, "technical_soundness": 6}')
    assert data["novelty"] == 7


def test_repairs_fenced_json():
    data = extract_json_object('```json\n{"novelty": 7,}\n```')
    assert data["novelty"] == 7


def test_fallback_on_invalid_json():
    review = repair_or_retry_invalid_json(lambda: "not json", persona="x", retries=0)
    assert review.short_rationale == "parse failed"
    assert review.confidence == 1

