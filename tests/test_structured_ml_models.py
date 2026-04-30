from __future__ import annotations

import json
from pathlib import Path

from koala_strategy.llm.structured_reviewer import REVIEW_AXES
from koala_strategy.models import ml_models


def _feature_row(paper_id: str, score: float, label: int) -> dict:
    row = {
        "paper_id": paper_id,
        "accept_label": label,
        "self_overall_accept_probability": score / 10.0,
        "ext_reviews_available": 1.0,
        "ext_mean_reliability": 0.6,
    }
    for axis in REVIEW_AXES:
        row[f"self_{axis}_score"] = score
        row[f"self_{axis}_risk"] = 1.0 - score / 10.0
        row[f"self_{axis}_confidence"] = 0.7
        row[f"ext_{axis}_score"] = score - 1.0
        row[f"ext_{axis}_risk"] = 1.0 - (score - 1.0) / 10.0
        row[f"ext_{axis}_confidence"] = 0.6
        row[f"gap_{axis}_score"] = -1.0
        row[f"gap_{axis}_risk"] = 0.1
    return row


def test_train_structured_models_with_logistic_fallback(tmp_path: Path) -> None:
    rows = [
        _feature_row(f"p{i}", score=2.0 + i, label=0 if i < 4 else 1)
        for i in range(8)
    ]

    result = ml_models.train_structured_verdict_models(rows, output_dir=tmp_path, n_folds=2, random_seed=7)

    assert "logistic" in result["models"]
    assert result["models"]["logistic"]["metrics"]["brier"] >= 0.0
    assert (tmp_path / "structured_logistic.pkl").exists()
    assert (tmp_path / "structured_logistic_raw_oof_predictions.npy").exists()
    assert (tmp_path / "structured_logistic_calibrated_oof_predictions.npy").exists()
    assert (tmp_path / "structured_model_metrics.json").exists()
    assert (tmp_path / "structured_ensemble_weighted.pkl").exists()
    assert (tmp_path / "structured_ensemble_weighted_raw_oof_predictions.npy").exists()
    assert (tmp_path / "structured_ensemble_weighted_calibrated_oof_predictions.npy").exists()
    assert (tmp_path / "structured_ensemble_metrics.json").exists()
    assert result["ensemble"]["weights"]
    assert abs(sum(result["ensemble"]["weights"].values()) - 1.0) < 1e-6
    assert all(weight >= 0.0 for weight in result["ensemble"]["weights"].values())
    assert result["feature_columns"]


def test_load_feature_rows_joins_external_rows(tmp_path: Path) -> None:
    self_path = tmp_path / "self.jsonl"
    external_path = tmp_path / "external.jsonl"
    self_path.write_text(
        json.dumps({"paper_id": "p1", "accept_label": 1, "self_a": 1.0}) + "\n",
        encoding="utf-8",
    )
    external_path.write_text(
        json.dumps({"paper_id": "p1", "ext_b": 2.0}) + "\n",
        encoding="utf-8",
    )

    rows = ml_models.load_structured_feature_rows(self_path, external_path)

    assert rows == [{"paper_id": "p1", "accept_label": 1, "self_a": 1.0, "ext_b": 2.0}]


def test_train_structured_models_uses_train_split_only(tmp_path: Path) -> None:
    rows = [
        _feature_row("train_reject_1", score=2.0, label=0) | {"split": "train"},
        _feature_row("train_reject_2", score=3.0, label=0) | {"split": "train"},
        _feature_row("train_accept_1", score=7.0, label=1) | {"split": "train"},
        _feature_row("train_accept_2", score=8.0, label=1) | {"split": "train"},
        _feature_row("test_reject", score=1.0, label=0) | {"split": "test"},
        _feature_row("test_accept", score=9.0, label=1) | {"split": "test"},
    ]

    result = ml_models.train_structured_verdict_models(rows, output_dir=tmp_path, n_folds=2, random_seed=7)

    assert result["num_examples"] == 4
    assert result["num_total_rows"] == 6
    assert result["num_accept"] == 2
    assert result["num_reject"] == 2
