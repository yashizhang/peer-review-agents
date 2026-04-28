from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.stats import pearsonr, spearmanr
from sklearn.calibration import IsotonicRegression
from sklearn.ensemble import ExtraTreesClassifier, HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression, SGDClassifier
from sklearn.metrics import average_precision_score, brier_score_loss, log_loss, roc_auc_score
from sklearn.model_selection import StratifiedKFold
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from koala_strategy.config import load_config, resolve_path
from koala_strategy.data.iclr_loader import load_iclr_examples, load_public_papers, load_test_labels
from koala_strategy.models.fulltext_evidence_model import (
    FullTextEvidenceModel,
    build_fulltext_feature_frame,
    parsed_payload_for_paper,
    select_train_subset,
)
from koala_strategy.models.predict_paper_only import load_model_artifacts
from koala_strategy.utils import dump_json, ensure_dir


def _clip(p: np.ndarray) -> np.ndarray:
    return np.clip(np.asarray(p, dtype=float), 1e-6, 1 - 1e-6)


def metric_summary(y: np.ndarray, p: np.ndarray) -> dict[str, float]:
    y = np.asarray(y, dtype=int)
    p = _clip(p)
    k = max(1, int(round(0.27 * len(y))))
    top = np.argsort(-p)[:k]
    return {
        "pearson": float(pearsonr(p, y).statistic),
        "spearman": float(spearmanr(p, y).statistic),
        "auroc": float(roc_auc_score(y, p)),
        "auprc": float(average_precision_score(y, p)),
        "brier": float(brier_score_loss(y, p)),
        "log_loss": float(log_loss(y, p, labels=[0, 1])),
        "top_27_percent_precision": float(y[top].mean()),
        "mean_pred": float(p.mean()),
    }


def _text_for_payload(payload: dict[str, Any]) -> str:
    sections = payload.get("sections") or {}
    table_bits = []
    for item in payload.get("table_evidence") or []:
        context = " ".join(str(item.get("caption_or_context", "")).split())
        table_bits.append(context[:2000])
    bits = [
        payload.get("title", ""),
        payload.get("abstract", ""),
        sections.get("introduction", "")[:8000],
        sections.get("method", "")[:10000],
        sections.get("methods", "")[:10000],
        sections.get("approach", "")[:10000],
        sections.get("experiments", "")[:14000],
        sections.get("results", "")[:10000],
        "\n".join(table_bits[:12]),
        (payload.get("full_text") or "")[:50000],
    ]
    return "\n\n".join(str(x) for x in bits if x)


def load_experiment_frames(train_limit: int = 1200) -> dict[str, Any]:
    cfg = load_config()
    artifacts = load_model_artifacts(cfg)
    examples = select_train_subset(load_iclr_examples(config=cfg), train_limit, int(cfg["models"].get("random_seed", 42)))
    papers = load_public_papers(config=cfg)
    labels = load_test_labels(config=cfg)
    labeled = [p for p in papers if p.paper_id in labels]
    X_train, train_records = build_fulltext_feature_frame(examples, artifacts)
    X_test, test_records = build_fulltext_feature_frame(labeled, artifacts)
    y_train = np.asarray([1 if r.decision == "accept" else 0 for r in train_records], dtype=int)
    y_test = np.asarray([int(labels[r.paper_id]["accept_label"]) for r in test_records], dtype=int)
    train_texts = [_text_for_payload(parsed_payload_for_paper(r.paper_id) or {}) for r in train_records]
    test_texts = [_text_for_payload(parsed_payload_for_paper(r.paper_id) or {}) for r in test_records]
    return {
        "config": cfg,
        "X_train": X_train,
        "X_test": X_test,
        "y_train": y_train,
        "y_test": y_test,
        "train_records": train_records,
        "test_records": test_records,
        "train_texts": train_texts,
        "test_texts": test_texts,
    }


def train_tabular_oof(model_type: str, X_train: pd.DataFrame, y_train: np.ndarray, X_test: pd.DataFrame, seed: int = 42) -> tuple[np.ndarray, np.ndarray, Any]:
    folds = min(5, max(2, np.bincount(y_train).min()))
    oof = np.zeros(len(y_train), dtype=float)
    skf = StratifiedKFold(n_splits=folds, shuffle=True, random_state=seed)
    for tr, va in skf.split(X_train, y_train):
        model = FullTextEvidenceModel(model_type=model_type, random_seed=seed).fit(X_train.iloc[tr], y_train[tr])
        oof[va] = model.predict_proba(X_train.iloc[va])
    final = FullTextEvidenceModel(model_type=model_type, random_seed=seed).fit(X_train, y_train)
    return oof, final.predict_proba(X_test), final


def train_extra_trees_oof(X_train: pd.DataFrame, y_train: np.ndarray, X_test: pd.DataFrame, seed: int = 42) -> tuple[np.ndarray, np.ndarray, Any]:
    folds = min(5, max(2, np.bincount(y_train).min()))
    oof = np.zeros(len(y_train), dtype=float)
    skf = StratifiedKFold(n_splits=folds, shuffle=True, random_state=seed)
    for tr, va in skf.split(X_train, y_train):
        model = ExtraTreesClassifier(
            n_estimators=600,
            max_depth=None,
            min_samples_leaf=5,
            max_features=0.7,
            class_weight="balanced",
            random_state=seed,
            n_jobs=-1,
        )
        model.fit(X_train.iloc[tr], y_train[tr])
        oof[va] = model.predict_proba(X_train.iloc[va])[:, 1]
    final = ExtraTreesClassifier(
        n_estimators=600,
        max_depth=None,
        min_samples_leaf=5,
        max_features=0.7,
        class_weight="balanced",
        random_state=seed,
        n_jobs=-1,
    ).fit(X_train, y_train)
    return oof, final.predict_proba(X_test)[:, 1], final


def train_tfidf_oof(train_texts: list[str], y_train: np.ndarray, test_texts: list[str], seed: int = 42) -> tuple[np.ndarray, np.ndarray, Any]:
    folds = min(5, max(2, np.bincount(y_train).min()))
    oof = np.zeros(len(y_train), dtype=float)
    skf = StratifiedKFold(n_splits=folds, shuffle=True, random_state=seed)
    for tr, va in skf.split(np.zeros(len(y_train)), y_train):
        vectorizer = TfidfVectorizer(
            max_features=100000,
            ngram_range=(1, 2),
            min_df=2,
            max_df=0.95,
            sublinear_tf=True,
            strip_accents="unicode",
        )
        clf = SGDClassifier(
            loss="log_loss",
            alpha=2e-5,
            penalty="elasticnet",
            l1_ratio=0.05,
            max_iter=40,
            tol=1e-3,
            average=True,
            class_weight="balanced",
            random_state=seed,
        )
        Xtr = vectorizer.fit_transform([train_texts[i] for i in tr])
        clf.fit(Xtr, y_train[tr])
        oof[va] = clf.predict_proba(vectorizer.transform([train_texts[i] for i in va]))[:, 1]
    vectorizer = TfidfVectorizer(
        max_features=100000,
        ngram_range=(1, 2),
        min_df=2,
        max_df=0.95,
        sublinear_tf=True,
        strip_accents="unicode",
    )
    clf = SGDClassifier(
        loss="log_loss",
        alpha=2e-5,
        penalty="elasticnet",
        l1_ratio=0.05,
        max_iter=40,
        tol=1e-3,
        average=True,
        class_weight="balanced",
        random_state=seed,
    )
    X = vectorizer.fit_transform(train_texts)
    clf.fit(X, y_train)
    test_pred = clf.predict_proba(vectorizer.transform(test_texts))[:, 1]
    return oof, test_pred, {"vectorizer": vectorizer, "classifier": clf}


def platt_calibrate(oof: np.ndarray, y: np.ndarray, test_pred: np.ndarray) -> tuple[np.ndarray, Any]:
    lr = LogisticRegression(C=1.0, max_iter=1000)
    lr.fit(np.log(_clip(oof) / (1 - _clip(oof))).reshape(-1, 1), y)
    x = np.log(_clip(test_pred) / (1 - _clip(test_pred))).reshape(-1, 1)
    return lr.predict_proba(x)[:, 1], lr


def isotonic_calibrate(oof: np.ndarray, y: np.ndarray, test_pred: np.ndarray) -> tuple[np.ndarray, Any]:
    iso = IsotonicRegression(out_of_bounds="clip")
    iso.fit(_clip(oof), y)
    return np.asarray(iso.predict(_clip(test_pred))), iso


def optimize_convex_weights(train_preds: np.ndarray, y: np.ndarray) -> np.ndarray:
    n = train_preds.shape[1]

    def objective(raw: np.ndarray) -> float:
        e = np.exp(raw - raw.max())
        w = e / e.sum()
        p = _clip(train_preds @ w)
        return float(log_loss(y, p, labels=[0, 1]))

    result = minimize(objective, np.zeros(n), method="BFGS")
    e = np.exp(result.x - result.x.max())
    return e / e.sum()


def run_experiment_suite(train_limit: int = 1200) -> dict[str, Any]:
    data = load_experiment_frames(train_limit)
    cfg = data["config"]
    seed = int(cfg["models"].get("random_seed", 42))
    X_train = data["X_train"]
    X_test = data["X_test"]
    y_train = data["y_train"]
    y_test = data["y_test"]

    candidates: dict[str, dict[str, Any]] = {
        "base": {"oof": X_train["base_p_accept"].to_numpy(), "test": X_test["base_p_accept"].to_numpy()}
    }

    artifacts: dict[str, Any] = {}
    for model_type in ["logreg", "hgb", "rf"]:
        oof, test, model = train_tabular_oof(model_type, X_train, y_train, X_test, seed)
        candidates[f"tab_{model_type}"] = {"oof": oof, "test": test}
        artifacts[f"tab_{model_type}"] = model
    oof, test, model = train_extra_trees_oof(X_train, y_train, X_test, seed)
    candidates["tab_extratrees"] = {"oof": oof, "test": test}
    artifacts["tab_extratrees"] = model
    oof, test, model = train_tfidf_oof(data["train_texts"], y_train, data["test_texts"], seed)
    candidates["tfidf_sgd"] = {"oof": oof, "test": test}
    artifacts["tfidf_sgd"] = model

    results: dict[str, Any] = {
        "num_train": int(len(y_train)),
        "num_test": int(len(y_test)),
        "candidate_metrics": {},
        "calibrated_metrics": {},
        "ensemble_metrics": {},
    }

    for name, pred in candidates.items():
        results["candidate_metrics"][name] = {
            "train_oof": metric_summary(y_train, pred["oof"]),
            "test": metric_summary(y_test, pred["test"]),
        }
        platt_test, platt = platt_calibrate(pred["oof"], y_train, pred["test"])
        iso_test, iso = isotonic_calibrate(pred["oof"], y_train, pred["test"])
        results["calibrated_metrics"][f"{name}_platt"] = {"test": metric_summary(y_test, platt_test)}
        results["calibrated_metrics"][f"{name}_isotonic"] = {"test": metric_summary(y_test, iso_test)}
        candidates[f"{name}_platt"] = {"oof": pred["oof"], "test": platt_test}
        candidates[f"{name}_isotonic"] = {"oof": pred["oof"], "test": iso_test}

    stack_names = ["base", "tab_logreg", "tab_hgb", "tab_rf", "tab_extratrees", "tfidf_sgd"]
    train_stack = np.column_stack([_clip(candidates[name]["oof"]) for name in stack_names])
    test_stack = np.column_stack([_clip(candidates[name]["test"]) for name in stack_names])

    weights = optimize_convex_weights(train_stack, y_train)
    p_weighted = _clip(test_stack @ weights)
    results["ensemble_metrics"]["weighted_logloss_oof"] = {
        "weights": {name: float(w) for name, w in zip(stack_names, weights)},
        "test": metric_summary(y_test, p_weighted),
    }

    meta = Pipeline(
        [
            ("scale", StandardScaler()),
            ("lr", LogisticRegression(C=0.5, max_iter=1000, class_weight="balanced", random_state=seed)),
        ]
    )
    meta.fit(np.column_stack([np.log(_clip(train_stack[:, i]) / (1 - _clip(train_stack[:, i]))) for i in range(train_stack.shape[1])]), y_train)
    test_meta_x = np.column_stack([np.log(_clip(test_stack[:, i]) / (1 - _clip(test_stack[:, i]))) for i in range(test_stack.shape[1])])
    p_meta = meta.predict_proba(test_meta_x)[:, 1]
    results["ensemble_metrics"]["logit_stacker"] = {"test": metric_summary(y_test, p_meta)}

    out_dir = ensure_dir(resolve_path(cfg, "model_dir"))
    dump_json(out_dir / "experiment_suite_metrics.json", results)
    joblib.dump(
        {
            "stack_names": stack_names,
            "weights": weights,
            "meta": meta,
            "artifacts": artifacts,
        },
        out_dir / "experiment_suite_artifacts.pkl",
    )
    return results

