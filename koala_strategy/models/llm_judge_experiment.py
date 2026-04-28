from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from scipy.stats import pearsonr, spearmanr
from sklearn.metrics import average_precision_score, brier_score_loss, log_loss, roc_auc_score

from koala_strategy.config import load_config, resolve_path
from koala_strategy.data.iclr_loader import load_public_papers, load_test_labels
from koala_strategy.llm.review_judge import llm_probability_for_blend, run_llm_review_judge
from koala_strategy.models.export_bundle import generate_prediction_bundle
from koala_strategy.models.predict_paper_only import load_model_artifacts
from koala_strategy.utils import dump_json, ensure_dir


def _metrics(y: np.ndarray, p: np.ndarray) -> dict[str, float]:
    y = np.asarray(y, dtype=int)
    p = np.clip(np.asarray(p, dtype=float), 1e-6, 1 - 1e-6)
    if len(np.unique(y)) < 2:
        auroc = float("nan")
        auprc = float("nan")
    else:
        auroc = float(roc_auc_score(y, p))
        auprc = float(average_precision_score(y, p))
    k = max(1, int(round(0.27 * len(y))))
    top = np.argsort(-p)[:k]
    return {
        "pearson": float(pearsonr(p, y).statistic) if len(y) > 1 else float("nan"),
        "spearman": float(spearmanr(p, y).statistic) if len(y) > 1 else float("nan"),
        "auroc": auroc,
        "auprc": auprc,
        "brier": float(brier_score_loss(y, p)),
        "log_loss": float(log_loss(y, p, labels=[0, 1])),
        "top_27_percent_precision": float(y[top].mean()),
        "mean_pred": float(p.mean()),
    }


def _select_indices(p: np.ndarray, y: np.ndarray, limit: int, mode: str, seed: int) -> list[int]:
    rng = np.random.default_rng(seed)
    n = len(p)
    limit = min(limit, n)
    if mode == "uncertain":
        return list(np.argsort(np.abs(p - 0.5))[:limit])
    if mode == "spread":
        order = np.argsort(p)
        positions = np.linspace(0, n - 1, limit).round().astype(int)
        return list(dict.fromkeys(int(order[pos]) for pos in positions))[:limit]
    if mode == "balanced_uncertain":
        order = list(np.argsort(np.abs(p - 0.5)))
        pos = [idx for idx in order if y[idx] == 1][: max(1, limit // 2)]
        neg = [idx for idx in order if y[idx] == 0][: limit - len(pos)]
        selected = pos + neg
        rng.shuffle(selected)
        return selected[:limit]
    return list(rng.choice(np.arange(n), size=limit, replace=False))


def _blend_predictions(base: np.ndarray, llm: np.ndarray, conf: np.ndarray) -> dict[str, np.ndarray]:
    out: dict[str, np.ndarray] = {
        "base": base,
        "llm": llm,
    }
    for alpha in [0.10, 0.20, 0.30, 0.40, 0.50]:
        out[f"blend_alpha_{alpha:.2f}"] = np.clip((1.0 - alpha) * base + alpha * llm, 1e-6, 1 - 1e-6)
    for max_alpha in [0.25, 0.40, 0.60]:
        alpha_i = max_alpha * np.clip(conf, 0.0, 1.0)
        out[f"confidence_blend_{max_alpha:.2f}"] = np.clip((1.0 - alpha_i) * base + alpha_i * llm, 1e-6, 1 - 1e-6)
    disagreement = np.abs(llm - base)
    alpha_gate = np.where((conf >= 0.65) & (disagreement >= 0.12), 0.40, 0.10)
    out["gated_confident_blend"] = np.clip((1.0 - alpha_gate) * base + alpha_gate * llm, 1e-6, 1 - 1e-6)
    return out


def run_llm_judge_subset_experiment(
    limit: int = 24,
    selection: str = "balanced_uncertain",
    force: bool = False,
    seed: int = 42,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = config or load_config()
    papers = load_public_papers(config=cfg)
    labels = load_test_labels(config=cfg)
    labeled = [paper for paper in papers if paper.paper_id in labels]
    artifacts = load_model_artifacts(cfg)
    bundles = [generate_prediction_bundle(paper, artifacts, cfg, save=True) for paper in labeled]
    y_all = np.asarray([int(labels[paper.paper_id]["accept_label"]) for paper in labeled], dtype=int)
    p_all = np.asarray([float(bundle.paper_only.get("p_accept", 0.5)) for bundle in bundles], dtype=float)
    indices = _select_indices(p_all, y_all, limit, selection, seed)

    rows: list[dict[str, Any]] = []
    for idx in indices:
        paper = labeled[idx]
        bundle = bundles[idx]
        judge = run_llm_review_judge(paper, bundle, config=cfg, force=force)
        rows.append(
            {
                "paper_id": paper.paper_id,
                "title": paper.title,
                "label": int(labels[paper.paper_id]["accept_label"]),
                "base_p": float(bundle.paper_only.get("p_accept", 0.5)),
                "llm_p": llm_probability_for_blend(judge),
                "llm_accept_probability": float(judge.get("accept_probability", 0.5)),
                "llm_verdict_score": float(judge.get("verdict_score", 5.0)),
                "llm_confidence": float(judge.get("confidence", 0.5)),
                "llm_fallback": bool(judge.get("fallback", False)),
                "strongest_accept_signal": judge.get("strongest_accept_signal", ""),
                "strongest_reject_signal": judge.get("strongest_reject_signal", ""),
                "short_rationale": judge.get("short_rationale", ""),
            }
        )

    df = pd.DataFrame(rows)
    y = df["label"].to_numpy(dtype=int)
    base = df["base_p"].to_numpy(dtype=float)
    llm = df["llm_p"].to_numpy(dtype=float)
    conf = df["llm_confidence"].to_numpy(dtype=float)
    predictions = _blend_predictions(base, llm, conf)
    metrics = {name: _metrics(y, pred) for name, pred in predictions.items()}
    best_by_log_loss = min(metrics, key=lambda name: metrics[name]["log_loss"])
    best_by_auroc = max(metrics, key=lambda name: -1 if np.isnan(metrics[name]["auroc"]) else metrics[name]["auroc"])
    out_dir = ensure_dir(resolve_path(cfg, "model_dir") / "llm_judge")
    df.to_csv(out_dir / f"subset_{selection}_{len(df)}.csv", index=False)
    result = {
        "limit": limit,
        "selection": selection,
        "num_examples": len(df),
        "num_accept": int(y.sum()),
        "num_reject": int(len(y) - y.sum()),
        "model": cfg.get("models", {}).get("codex_model"),
        "metrics": metrics,
        "best_by_log_loss": best_by_log_loss,
        "best_by_auroc": best_by_auroc,
        "predictions_csv": str(out_dir / f"subset_{selection}_{len(df)}.csv"),
    }
    dump_json(out_dir / f"subset_{selection}_{len(df)}_metrics.json", result)
    return result
