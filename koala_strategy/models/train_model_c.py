from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import StratifiedKFold
from sklearn.preprocessing import StandardScaler

from koala_strategy.config import load_config, resolve_path
from koala_strategy.discussion.discussion_features import extract_discussion_features_from_reviews
from koala_strategy.models.calibrate import classification_metrics, safe_logit
from koala_strategy.utils import dump_json, ensure_dir


def review_discussion_frame(records: list[Any]) -> pd.DataFrame:
    rows = []
    for record in records:
        reviews = getattr(record, "official_reviews", None)
        if reviews is None and isinstance(record, dict):
            reviews = record.get("official_reviews", [])
        rows.append(extract_discussion_features_from_reviews(reviews or []))
    return pd.DataFrame(rows).fillna(0.0)


@dataclass
class DiscussionModel:
    random_seed: int = 42

    def __post_init__(self) -> None:
        self.scaler = StandardScaler()
        self.classifier = LogisticRegression(C=0.7, max_iter=1000, random_state=self.random_seed)
        self.feature_columns: list[str] = []

    def make_matrix(self, p_prior: np.ndarray, discussion_df: pd.DataFrame, fit: bool = False) -> np.ndarray:
        df = discussion_df.copy()
        df.insert(0, "paper_prior_logit", safe_logit(np.asarray(p_prior, dtype=float)))
        if fit:
            self.feature_columns = list(df.columns)
            return self.scaler.fit_transform(df.values)
        df = df.reindex(columns=self.feature_columns, fill_value=0.0)
        return self.scaler.transform(df.values)

    def fit(self, p_prior: np.ndarray, discussion_df: pd.DataFrame, y: np.ndarray) -> "DiscussionModel":
        X = self.make_matrix(p_prior, discussion_df, fit=True)
        self.classifier.fit(X, y)
        return self

    def predict_proba(self, p_prior: np.ndarray, discussion_df: pd.DataFrame) -> np.ndarray:
        X = self.make_matrix(p_prior, discussion_df, fit=False)
        return self.classifier.predict_proba(X)[:, 1]


def train_model_c(
    records: list[Any],
    y: np.ndarray,
    p_paper_only_oof: np.ndarray,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = config or load_config()
    y = np.asarray(y, dtype=int)
    df = review_discussion_frame(records)
    random_seed = int(cfg.get("models", {}).get("random_seed", 42))
    oof = np.zeros(len(y), dtype=float)
    skf = StratifiedKFold(n_splits=int(cfg.get("models", {}).get("n_folds", 5)), shuffle=True, random_state=random_seed)
    for tr, va in skf.split(df, y):
        fold = DiscussionModel(random_seed=random_seed)
        fold.fit(p_paper_only_oof[tr], df.iloc[tr], y[tr])
        oof[va] = fold.predict_proba(p_paper_only_oof[va], df.iloc[va])
    model = DiscussionModel(random_seed=random_seed).fit(p_paper_only_oof, df, y)
    metrics = classification_metrics(y, oof)
    coefs = {}
    for name, coef in zip(model.feature_columns, model.classifier.coef_[0]):
        coefs[name] = float(coef)
    metrics["coefficients"] = coefs
    out_dir = ensure_dir(resolve_path(cfg, "model_dir"))
    joblib.dump(model, out_dir / "model_c.pkl")
    np.save(out_dir / "model_c_oof_predictions.npy", oof)
    dump_json(out_dir / "model_c_metrics.json", metrics)
    return {"model": model, "oof": oof, "metrics": metrics}

