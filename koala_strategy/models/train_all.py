from __future__ import annotations

from typing import Any

import joblib
import numpy as np

from koala_strategy.config import load_config, resolve_path
from koala_strategy.data.dataset_builder import build_iclr_dataset
from koala_strategy.data.iclr_loader import load_iclr_examples, load_public_papers, load_test_labels
from koala_strategy.models.calibrate import classification_metrics
from koala_strategy.models.export_bundle import generate_prediction_bundle
from koala_strategy.models.feature_schema import pseudo_review_frame
from koala_strategy.models.predict_paper_only import load_model_artifacts, predict_paper_only
from koala_strategy.models.train_model_a import train_model_a_oof
from koala_strategy.models.train_model_b import train_model_b
from koala_strategy.models.train_model_c import train_model_c
from koala_strategy.models.train_stacker import train_stacker_and_calibrator
from koala_strategy.utils import dump_json, ensure_dir


def evaluate_global_test(config: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = config or load_config()
    papers = load_public_papers(config=cfg)
    labels = load_test_labels(config=cfg)
    labeled = [paper for paper in papers if paper.paper_id in labels]
    y = np.asarray([int(labels[p.paper_id]["accept_label"]) for p in labeled], dtype=int)
    artifacts = load_model_artifacts(cfg)
    pred = predict_paper_only(labeled, artifacts)
    metrics = classification_metrics(y, pred["p_accept"])
    score_errors = []
    bundles = []
    for paper in labeled:
        bundle = generate_prediction_bundle(paper, artifacts, cfg, save=True)
        bundles.append(bundle)
        label = labels[paper.paper_id]
        if "suggested_verdict_score" in label:
            pred_score = sum(bundle.paper_only["recommended_score_range"]) / 2.0
            score_errors.append(abs(float(label["suggested_verdict_score"]) - pred_score))
    if score_errors:
        metrics["suggested_score_mae"] = float(np.mean(score_errors))
    metrics["num_test_examples"] = len(labeled)
    metrics["mean_predicted_accept"] = float(np.mean(pred["p_accept"]))
    metrics["mean_uncertainty"] = float(np.mean(pred["uncertainty"]))
    dump_json(resolve_path(cfg, "model_dir") / "global_test_metrics.json", metrics)
    return metrics


def train_all(config: dict[str, Any] | None = None, evaluate: bool = True) -> dict[str, Any]:
    cfg = config or load_config()
    ensure_dir(resolve_path(cfg, "model_dir"))
    dataset_summary = build_iclr_dataset(cfg)
    examples = load_iclr_examples(config=cfg)
    y = np.asarray([1 if ex.decision == "accept" else 0 for ex in examples], dtype=int)
    model_a, p_a_oof, metrics_a = train_model_a_oof(
        examples,
        y,
        n_folds=int(cfg.get("models", {}).get("n_folds", 5)),
        random_seed=int(cfg.get("models", {}).get("random_seed", 42)),
    )
    out_dir = resolve_path(cfg, "model_dir")
    joblib.dump(model_a, out_dir / "model_a.pkl")
    np.save(out_dir / "model_a_oof_predictions.npy", p_a_oof)
    dump_json(out_dir / "model_a_metrics.json", metrics_a)

    result_b = train_model_b(examples, y, cfg)
    p_b_oof = result_b["oof"]
    metrics_b = result_b["metrics"]
    panel_df = pseudo_review_frame(examples)
    panel_disagreement = panel_df.get("panel_disagreement", 0.0).to_numpy(dtype=float)

    result_stack = train_stacker_and_calibrator(p_a_oof, p_b_oof, y, panel_disagreement, examples, cfg)
    result_c = train_model_c(examples, y, result_stack["oof"], cfg)

    metrics: dict[str, Any] = {
        "dataset": dataset_summary,
        "model_a": metrics_a,
        "model_b": metrics_b,
        "stacker": result_stack["metrics"],
        "model_c": result_c["metrics"],
    }
    if evaluate:
        metrics["global_test"] = evaluate_global_test(cfg)
    dump_json(out_dir / "metrics.json", metrics)
    test = metrics.get("global_test", {})
    card = f"""# Calibration Card

Model version: {cfg.get("models", {}).get("version", "iclr26_v1")}

## Summary

ICLR26 paper-only predictor with a heuristic pseudo-review stacker and a discussion-aware proxy model.

## Leakage Policy

Model A/B use only paper-visible fields. Model C uses official reviews only as proxy discussion training features, not for online paper-only inference.

## Held-Out Global Test

- AUROC: {test.get("auroc", 0):.4f}
- AUPRC: {test.get("auprc", 0):.4f}
- Brier: {test.get("brier", 0):.4f}
- Log loss: {test.get("log_loss", 0):.4f}
- Top 27% precision: {test.get("top_27_percent_precision", 0):.4f}
- Suggested score MAE: {test.get("suggested_score_mae", 0):.4f}

## Training Set

- Examples: {metrics["dataset"]["num_examples"]}
- Accept: {metrics["dataset"]["num_accept"]}
- Reject: {metrics["dataset"]["num_reject"]}
"""
    (out_dir / "calibration_card.md").write_text(card, encoding="utf-8")
    return metrics
