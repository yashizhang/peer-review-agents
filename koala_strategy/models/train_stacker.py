from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import joblib
import numpy as np
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import StratifiedKFold

from koala_strategy.config import load_config, resolve_path
from koala_strategy.models.calibrate import classification_metrics, safe_logit
from koala_strategy.models.uncertainty import combine_uncertainty
from koala_strategy.utils import dump_json, ensure_dir, normalize_domain


def make_stack_features(p_a: np.ndarray, p_b: np.ndarray, panel_disagreement: np.ndarray | None = None) -> np.ndarray:
    p_a = np.asarray(p_a, dtype=float)
    p_b = np.asarray(p_b, dtype=float)
    if panel_disagreement is None:
        panel_disagreement = np.zeros_like(p_a)
    panel_disagreement = np.asarray(panel_disagreement, dtype=float)
    uncertainty = np.asarray([combine_uncertainty(a, b, d) for a, b, d in zip(p_a, p_b, panel_disagreement)])
    return np.column_stack(
        [
            safe_logit(p_a),
            safe_logit(p_b),
            np.abs(p_a - p_b),
            uncertainty,
            panel_disagreement,
            (p_a + p_b) / 2.0,
        ]
    )


@dataclass
class StackerModel:
    random_seed: int = 42

    def __post_init__(self) -> None:
        self.stacker = LogisticRegression(C=0.5, max_iter=1000, random_state=self.random_seed)
        self.calibrator = IsotonicRegression(out_of_bounds="clip")
        self.train_distribution: list[float] = []
        self.domain_distributions: dict[str, list[float]] = {}

    def fit(
        self,
        p_a: np.ndarray,
        p_b: np.ndarray,
        y: np.ndarray,
        panel_disagreement: np.ndarray | None = None,
        records: list[Any] | None = None,
    ) -> np.ndarray:
        X = make_stack_features(p_a, p_b, panel_disagreement)
        y = np.asarray(y, dtype=int)
        oof = np.zeros(len(y), dtype=float)
        splits = min(5, max(2, np.bincount(y).min()))
        skf = StratifiedKFold(n_splits=splits, shuffle=True, random_state=self.random_seed)
        for tr, va in skf.split(X, y):
            clf = LogisticRegression(C=0.5, max_iter=1000, random_state=self.random_seed)
            clf.fit(X[tr], y[tr])
            oof[va] = clf.predict_proba(X[va])[:, 1]
        self.stacker.fit(X, y)
        self.calibrator.fit(oof, y)
        calibrated = self.calibrator.predict(oof)
        self.train_distribution = [float(x) for x in calibrated]
        if records:
            by_domain: dict[str, list[float]] = {}
            for record, p in zip(records, calibrated):
                domains = getattr(record, "domains", None) or (record.get("domains", []) if isinstance(record, dict) else [])
                for domain in domains:
                    key = normalize_domain(domain) or str(domain)
                    by_domain.setdefault(key, []).append(float(p))
            self.domain_distributions = by_domain
        return calibrated

    def predict(self, p_a: np.ndarray, p_b: np.ndarray, panel_disagreement: np.ndarray | None = None) -> np.ndarray:
        X = make_stack_features(p_a, p_b, panel_disagreement)
        raw = self.stacker.predict_proba(X)[:, 1]
        return np.asarray(self.calibrator.predict(raw), dtype=float)


def train_stacker_and_calibrator(
    p_a_oof: np.ndarray,
    p_b_oof: np.ndarray,
    y: np.ndarray,
    panel_disagreement: np.ndarray | None = None,
    records: list[Any] | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = config or load_config()
    model = StackerModel(random_seed=int(cfg.get("models", {}).get("random_seed", 42)))
    p_cal = model.fit(p_a_oof, p_b_oof, y, panel_disagreement, records)
    metrics = classification_metrics(y, p_cal)
    out_dir = ensure_dir(resolve_path(cfg, "model_dir"))
    joblib.dump(model, out_dir / "stacker.pkl")
    np.save(out_dir / "paper_only_oof_predictions.npy", p_cal)
    dump_json(out_dir / "stacker_metrics.json", metrics)
    return {"model": model, "oof": p_cal, "metrics": metrics}

