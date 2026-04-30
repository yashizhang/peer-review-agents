from __future__ import annotations

import json

from koala_strategy.llm import review_evaluator
from koala_strategy.llm.structured_reviewer import REVIEW_AXES, validate_self_review


class FakeProvider:
    def __init__(self, content: str):
        self.content = content
        self.calls = 0

    def generate(self, prompt: str, *, model: str | None = None, temperature: float = 0.0) -> str:
        self.calls += 1
        return self.content


def _self_review() -> dict:
    return validate_self_review(
        {
            "axes": {
                axis: {"score": 6, "risk": 0.4, "confidence": 0.7, "rationale": "self"}
                for axis in REVIEW_AXES
            },
            "overall_accept_probability": 0.55,
        },
        paper_id="p1",
        model="m",
    )


def test_review_evaluator_prompt_drops_numeric_scores() -> None:
    prompt = review_evaluator.build_review_evaluator_prompt(
        paper_id="p1",
        title="Paper",
        self_review=_self_review(),
        official_reviews=[
            {
                "summary": "Good idea.",
                "strengths": "Strong experiments.",
                "weaknesses": "Missing baseline.",
                "scores": {"rating": 8, "confidence": 5},
            }
        ],
    )

    assert "Good idea" in prompt
    assert "Missing baseline" in prompt
    assert '"scores"' not in prompt
    assert '"rating"' not in prompt
    assert '"confidence": 5' not in prompt


def test_external_evaluator_skips_missing_reviews(tmp_path) -> None:
    provider = FakeProvider("{}")

    result = review_evaluator.evaluate_external_reviews_for_paper(
        paper_id="p1",
        title="Paper",
        self_review=_self_review(),
        official_reviews=[],
        provider=provider,
        cache_dir=tmp_path,
    )

    assert provider.calls == 0
    assert result["external_reviews_available"] is False
    assert result["num_external_reviews"] == 0
    assert result["reliability_summary"]["mean_reliability"] == 0.0


def test_external_evaluator_outputs_weighted_features_and_gaps(tmp_path) -> None:
    payload = {
        "review_reliabilities": [
            {"review_id": "r1", "reliability": 0.8},
            {"review_id": "r2", "reliability": 0.4},
        ],
        "weighted_axes": {
            axis: {"score": 4, "risk": 0.7, "confidence": 0.5}
            for axis in REVIEW_AXES
        },
        "reliability_summary": {"mean_reliability": 0.6, "review_disagreement": 0.2},
        "summary": "External reviews are more negative than self review.",
    }
    provider = FakeProvider(json.dumps(payload))

    result = review_evaluator.evaluate_external_reviews_for_paper(
        paper_id="p1",
        title="Paper",
        self_review=_self_review(),
        official_reviews=[{"note_id": "r1", "summary": "Concern"}, {"note_id": "r2", "summary": "Concern"}],
        provider=provider,
        cache_dir=tmp_path,
    )
    flat = review_evaluator.flatten_external_review_features(result)

    assert provider.calls == 1
    assert result["external_reviews_available"] is True
    assert flat["ext_mean_reliability"] == 0.6
    assert flat["ext_empirical_validation_score"] == 4.0
    assert flat["gap_empirical_validation_score"] == -2.0
    assert (tmp_path / "p1.json").exists()


def test_extract_review_evaluator_limits_cached_train_papers(tmp_path) -> None:
    dataset_dir = tmp_path / "koala_iclr2026"
    dataset_dir.mkdir()
    (dataset_dir / "global_train.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"paper_id": "not-cached", "title": "A", "accept_label": 0, "official_reviews": [{"summary": "x"}]}),
                json.dumps({"paper_id": "cached", "title": "B", "accept_label": 1, "official_reviews": [{"summary": "y"}]}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    cache_dir = tmp_path / "self_cache"
    cache_dir.mkdir()
    (cache_dir / "cached.json").write_text(json.dumps(_self_review()), encoding="utf-8")
    provider = FakeProvider(
        json.dumps(
            {
                "review_reliabilities": [{"review_id": "r1", "reliability": 0.7}],
                "weighted_axes": {axis: {"score": 5, "risk": 0.5, "confidence": 0.5} for axis in REVIEW_AXES},
            }
        )
    )

    summary = review_evaluator.extract_review_evaluator_features(
        self_review_cache_dir=cache_dir,
        output_path=tmp_path / "external.jsonl",
        cache_dir=tmp_path / "external_cache",
        limit=1,
        provider=provider,
        config={"paths": {"koala_dataset_dir": str(dataset_dir)}},
    )

    assert summary["num_papers"] == 1
    assert provider.calls == 1
