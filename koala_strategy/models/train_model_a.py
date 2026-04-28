from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
from scipy.sparse import csr_matrix, hstack
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import SGDClassifier
from sklearn.model_selection import StratifiedKFold
from sklearn.preprocessing import StandardScaler

from koala_strategy.config import load_config, resolve_path
from koala_strategy.data.iclr_loader import load_iclr_examples
from koala_strategy.models.calibrate import classification_metrics
from koala_strategy.models.feature_schema import structured_frame, text_corpus
from koala_strategy.utils import dump_json, ensure_dir


@dataclass
class PaperOnlyModel:
    max_features: int = 30000
    random_seed: int = 42

    def __post_init__(self) -> None:
        self.vectorizer = TfidfVectorizer(
            max_features=self.max_features,
            ngram_range=(1, 2),
            min_df=2,
            sublinear_tf=True,
            strip_accents="unicode",
        )
        self.scaler = StandardScaler()
        self.classifier = SGDClassifier(
            loss="log_loss",
            alpha=1e-5,
            penalty="elasticnet",
            l1_ratio=0.05,
            max_iter=20,
            tol=1e-3,
            average=True,
            random_state=self.random_seed,
        )
        self.numeric_columns: list[str] = []

    def _matrix(self, records: list[Any], fit: bool = False):
        texts = text_corpus(records)
        X_text = self.vectorizer.fit_transform(texts) if fit else self.vectorizer.transform(texts)
        df = structured_frame(records)
        if fit:
            self.numeric_columns = list(df.columns)
            X_num = self.scaler.fit_transform(df.values)
        else:
            df = df.reindex(columns=self.numeric_columns, fill_value=0.0)
            X_num = self.scaler.transform(df.values)
        return hstack([X_text, csr_matrix(X_num)], format="csr")

    def fit(self, records: list[Any], y: np.ndarray) -> "PaperOnlyModel":
        X = self._matrix(records, fit=True)
        self.classifier.fit(X, y)
        return self

    def predict_proba(self, records: list[Any]) -> np.ndarray:
        X = self._matrix(records, fit=False)
        return self.classifier.predict_proba(X)[:, 1]


def build_model_a_features(examples: list[Any]) -> tuple[Any, np.ndarray]:
    return structured_frame(examples), np.asarray([1 if ex.decision == "accept" else 0 for ex in examples], dtype=int)


def train_model_a_oof(records: list[Any], y: np.ndarray, n_folds: int = 5, random_seed: int = 42) -> tuple[PaperOnlyModel, np.ndarray, dict]:
    y = np.asarray(y, dtype=int)
    oof = np.zeros(len(records), dtype=float)
    splitter = StratifiedKFold(n_splits=n_folds, shuffle=True, random_state=random_seed)
    for train_idx, valid_idx in splitter.split(np.zeros(len(y)), y):
        fold_model = PaperOnlyModel(random_seed=random_seed)
        train_records = [records[i] for i in train_idx]
        valid_records = [records[i] for i in valid_idx]
        fold_model.fit(train_records, y[train_idx])
        oof[valid_idx] = fold_model.predict_proba(valid_records)
    final_model = PaperOnlyModel(random_seed=random_seed).fit(records, y)
    metrics = classification_metrics(y, oof)
    return final_model, oof, metrics


def train_model_a(config: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = config or load_config()
    examples = load_iclr_examples(config=cfg)
    y = np.asarray([1 if ex.decision == "accept" else 0 for ex in examples], dtype=int)
    model, oof, metrics = train_model_a_oof(
        examples,
        y,
        n_folds=int(cfg.get("models", {}).get("n_folds", 5)),
        random_seed=int(cfg.get("models", {}).get("random_seed", 42)),
    )
    out_dir = ensure_dir(resolve_path(cfg, "model_dir"))
    joblib.dump(model, out_dir / "model_a.pkl")
    np.save(out_dir / "model_a_oof_predictions.npy", oof)
    dump_json(out_dir / "model_a_metrics.json", metrics)
    return {"model": model, "oof": oof, "metrics": metrics}
