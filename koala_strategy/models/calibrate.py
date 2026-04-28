from __future__ import annotations

import numpy as np
from sklearn.metrics import average_precision_score, brier_score_loss, log_loss, roc_auc_score


def safe_logit(p: np.ndarray | float, eps: float = 1e-5) -> np.ndarray:
    arr = np.asarray(p, dtype=float)
    arr = np.clip(arr, eps, 1.0 - eps)
    return np.log(arr / (1.0 - arr))


def classification_metrics(y_true: np.ndarray, p_accept: np.ndarray) -> dict[str, float | list[dict[str, float]]]:
    y = np.asarray(y_true, dtype=int)
    p = np.clip(np.asarray(p_accept, dtype=float), 1e-6, 1 - 1e-6)
    metrics: dict[str, float | list[dict[str, float]]] = {
        "auroc": float(roc_auc_score(y, p)) if len(set(y.tolist())) > 1 else 0.5,
        "auprc": float(average_precision_score(y, p)) if len(set(y.tolist())) > 1 else float(y.mean()),
        "brier": float(brier_score_loss(y, p)),
        "log_loss": float(log_loss(y, p, labels=[0, 1])),
    }
    cutoff = np.quantile(p, 0.73)
    selected = p >= cutoff
    metrics["top_27_percent_precision"] = float(y[selected].mean()) if selected.any() else 0.0
    bins: list[dict[str, float]] = []
    for lo, hi in zip(np.linspace(0, 1, 11)[:-1], np.linspace(0, 1, 11)[1:]):
        mask = (p >= lo) & (p < hi if hi < 1 else p <= hi)
        if mask.any():
            bins.append(
                {
                    "lo": float(lo),
                    "hi": float(hi),
                    "count": int(mask.sum()),
                    "mean_pred": float(p[mask].mean()),
                    "empirical_accept": float(y[mask].mean()),
                }
            )
    metrics["calibration_bins"] = bins
    return metrics

