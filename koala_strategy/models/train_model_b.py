from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import joblib
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import StratifiedKFold
from sklearn.preprocessing import StandardScaler

from koala_strategy.config import load_config, resolve_path
from koala_strategy.data.iclr_loader import load_iclr_examples
from koala_strategy.models.calibrate import classification_metrics
from koala_strategy.models.feature_schema import pseudo_review_frame
from koala_strategy.utils import dump_json, ensure_dir


@dataclass
class PseudoReviewModel:
    random_seed: int = 42

    def __post_init__(self) -> None:
        self.scaler = StandardScaler()
        self.classifier = LogisticRegression(C=1.0, max_iter=1000, random_state=self.random_seed)
        self.feature_columns: list[str] = []

    def _matrix(self, records: list[Any], fit: bool = False) -> np.ndarray:
        df = pseudo_review_frame(records)
        return self._matrix_frame(df, fit=fit)

    def _matrix_frame(self, df, fit: bool = False) -> np.ndarray:
        if fit:
            self.feature_columns = list(df.columns)
            return self.scaler.fit_transform(df.values)
        df = df.reindex(columns=self.feature_columns, fill_value=0.0)
        return self.scaler.transform(df.values)

    def features(self, records: list[Any]) -> np.ndarray:
        return self._matrix(records, fit=False)

    def fit(self, records: list[Any], y: np.ndarray) -> "PseudoReviewModel":
        X = self._matrix(records, fit=True)
        self.classifier.fit(X, y)
        return self

    def fit_frame(self, df, y: np.ndarray) -> "PseudoReviewModel":
        X = self._matrix_frame(df, fit=True)
        self.classifier.fit(X, y)
        return self

    def predict_proba(self, records: list[Any]) -> np.ndarray:
        X = self._matrix(records, fit=False)
        return self.classifier.predict_proba(X)[:, 1]

    def predict_proba_frame(self, df) -> np.ndarray:
        X = self._matrix_frame(df, fit=False)
        return self.classifier.predict_proba(X)[:, 1]

    def feature_frame(self, records: list[Any]):
        return pseudo_review_frame(records).reindex(columns=self.feature_columns, fill_value=0.0)


def train_model_b(records: list[Any] | None = None, y: np.ndarray | None = None, config: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = config or load_config()
    if records is None:
        records = load_iclr_examples(config=cfg)
    if y is None:
        y = np.asarray([1 if ex.decision == "accept" else 0 for ex in records], dtype=int)
    y = np.asarray(y, dtype=int)
    n_folds = int(cfg.get("models", {}).get("n_folds", 5))
    random_seed = int(cfg.get("models", {}).get("random_seed", 42))
    df = pseudo_review_frame(records)
    oof = np.zeros(len(records), dtype=float)
    splitter = StratifiedKFold(n_splits=n_folds, shuffle=True, random_state=random_seed)
    for train_idx, valid_idx in splitter.split(np.zeros(len(y)), y):
        fold_model = PseudoReviewModel(random_seed=random_seed)
        fold_model.fit_frame(df.iloc[train_idx], y[train_idx])
        oof[valid_idx] = fold_model.predict_proba_frame(df.iloc[valid_idx])
    model = PseudoReviewModel(random_seed=random_seed).fit_frame(df, y)
    metrics = classification_metrics(y, oof)
    out_dir = ensure_dir(resolve_path(cfg, "model_dir"))
    joblib.dump(model, out_dir / "model_b.pkl")
    np.save(out_dir / "model_b_oof_predictions.npy", oof)
    dump_json(out_dir / "model_b_metrics.json", metrics)
    return {"model": model, "oof": oof, "metrics": metrics}
