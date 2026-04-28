from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

import joblib
import numpy as np
import pandas as pd
from scipy.sparse import csr_matrix, hstack
from scipy.stats import pearsonr, spearmanr
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression, SGDClassifier
from sklearn.metrics import average_precision_score, brier_score_loss, log_loss, roc_auc_score
from sklearn.model_selection import StratifiedKFold
from sklearn.preprocessing import StandardScaler
from sklearn.isotonic import IsotonicRegression

from koala_strategy.config import load_config, resolve_path
from koala_strategy.data.iclr_loader import load_iclr_examples, load_public_papers, load_test_labels
from koala_strategy.models.calibrate import classification_metrics, safe_logit
from koala_strategy.models.fulltext_evidence_model import (
    build_fulltext_feature_frame,
    parsed_payload_for_paper,
    select_train_subset,
)
from koala_strategy.models.predict_paper_only import load_model_artifacts, predict_paper_only
from koala_strategy.paper.text_sanitizer import sanitize_model_text, sanitized_fulltext_payload
from koala_strategy.schemas import ParsedPaperText
from koala_strategy.utils import dump_json, ensure_dir


SAFE_SUFFIX = "_safe"
LEAKY_TEXT_RE = re.compile(
    r"(?i)(acknowledg|author contribution|corresponding author|equal contribution|affiliation|"
    r"anonymous|openreview|accepted|published|camera[- ]?ready|under review|submitted|submission|"
    r"conference paper|paper decision|iclr\s*2026|work done during|rebuttal|withdrawn)"
)
EMAIL_RE = re.compile(r"(?i)\b[\w.+-]+@[\w.-]+\.[a-z]{2,}\b")
URL_RE = re.compile(r"(?i)\bhttps?://\S+")


def _paper_id(record: Any) -> str:
    if hasattr(record, "paper_id"):
        return str(record.paper_id)
    return str(record.get("paper_id") or record.get("id"))


def _trim(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    head = text[: int(max_chars * 0.75)]
    tail = text[-int(max_chars * 0.25) :]
    return head + "\n\n" + tail


def scrub_leaky_model_text(text: str) -> str:
    """Remove post-decision/version metadata before text models see a PDF."""
    return sanitize_model_text(text)


def payload_text(payload: dict[str, Any], mode: str) -> str:
    safe_mode = mode.endswith(SAFE_SUFFIX)
    if safe_mode:
        mode = mode[: -len(SAFE_SUFFIX)]
    title = str(payload.get("title") or "")
    abstract = str(payload.get("abstract") or "")
    full_text = str(payload.get("full_text") or "")
    sections = payload.get("sections") or {}
    table_text = "\n".join(str(t.get("caption_or_context", "")) for t in payload.get("table_evidence") or [])
    figure_text = "\n".join(payload.get("figure_captions") or [])
    if safe_mode:
        return _trim(sanitized_fulltext_payload({**payload, "sections": sections}, mode), 90000)
    if mode == "tables":
        return _trim("\n".join([title, abstract, table_text]), 40000)
    if mode == "sections":
        selected = []
        for key in ["abstract", "introduction", "method", "methods", "approach", "experiments", "results", "discussion", "limitations", "conclusion"]:
            if key in sections:
                selected.append(str(sections[key]))
        return _trim("\n".join([title, abstract, "\n".join(selected), table_text]), 70000)
    if mode == "evidence":
        return _trim("\n".join([title, abstract, table_text, figure_text]), 50000)
    if mode == "evidence_tables":
        return _trim("\n".join([title, abstract, table_text]), 50000)
    return _trim("\n".join([title, abstract, full_text]), 90000)


def parsed_records(records: list[Any]) -> tuple[list[Any], list[dict[str, Any]]]:
    usable_records: list[Any] = []
    payloads: list[dict[str, Any]] = []
    for record in records:
        payload = parsed_payload_for_paper(_paper_id(record))
        if payload:
            usable_records.append(record)
            payloads.append(payload)
    return usable_records, payloads


@dataclass
class SparseTextModel:
    mode: str = "sections"
    include_numeric: bool = True
    classifier: str = "logreg"
    max_features: int = 120000
    random_seed: int = 42
    c_value: float = 1.0

    def __post_init__(self) -> None:
        self.vectorizer = TfidfVectorizer(
            max_features=self.max_features,
            ngram_range=(1, 2),
            min_df=2,
            max_df=0.96,
            sublinear_tf=True,
            strip_accents="unicode",
            token_pattern=r"(?u)\b[\w][\w.-]{1,}\b",
        )
        self.scaler = StandardScaler()
        self.numeric_columns: list[str] = []
        if self.classifier == "sgd":
            self.model = SGDClassifier(
                loss="log_loss",
                alpha=2e-5,
                penalty="elasticnet",
                l1_ratio=0.02,
                max_iter=80,
                tol=1e-4,
                class_weight="balanced",
                average=True,
                random_state=self.random_seed,
            )
        else:
            self.model = LogisticRegression(
                C=self.c_value,
                solver="liblinear",
                max_iter=1000,
                class_weight="balanced",
                random_state=self.random_seed,
            )

    def _texts(self, payloads: list[dict[str, Any]]) -> list[str]:
        return [payload_text(payload, self.mode) for payload in payloads]

    def _matrix(self, payloads: list[dict[str, Any]], numeric_df: pd.DataFrame, fit: bool = False):
        X_text = self.vectorizer.fit_transform(self._texts(payloads)) if fit else self.vectorizer.transform(self._texts(payloads))
        if not self.include_numeric:
            return X_text
        if fit:
            self.numeric_columns = list(numeric_df.columns)
            X_num = self.scaler.fit_transform(numeric_df.values)
        else:
            X_num = self.scaler.transform(numeric_df.reindex(columns=self.numeric_columns, fill_value=0.0).values)
        return hstack([X_text, csr_matrix(X_num)], format="csr")

    def fit(self, payloads: list[dict[str, Any]], numeric_df: pd.DataFrame, y: np.ndarray) -> "SparseTextModel":
        self.model.fit(self._matrix(payloads, numeric_df, fit=True), y)
        return self

    def predict_proba(self, payloads: list[dict[str, Any]], numeric_df: pd.DataFrame) -> np.ndarray:
        return self.model.predict_proba(self._matrix(payloads, numeric_df, fit=False))[:, 1]


@dataclass
class TextEvidenceProductionModel:
    text_model: SparseTextModel
    blend_alpha: float
    name: str
    train_oof_metrics: dict[str, float]

    def predict_proba(self, payloads: list[dict[str, Any]], numeric_df: pd.DataFrame) -> np.ndarray:
        return self.text_model.predict_proba(payloads, numeric_df)


@dataclass
class FastTextEvidenceProductionModel:
    mode: str
    vectorizer: TfidfVectorizer
    scaler: StandardScaler
    model: SGDClassifier
    numeric_columns: list[str]
    blend_alpha: float
    calibration_kind: str
    calibrator: Any | None
    name: str = "fast_text_evidence"

    def _matrix(self, payloads: list[dict[str, Any]], numeric_df: pd.DataFrame):
        texts = [payload_text(payload, self.mode) for payload in payloads]
        X_text = self.vectorizer.transform(texts)
        X_num = self.scaler.transform(numeric_df.reindex(columns=self.numeric_columns, fill_value=0.0).values)
        return hstack([X_text, csr_matrix(X_num)], format="csr")

    def _calibrate(self, p: np.ndarray) -> np.ndarray:
        p = np.clip(np.asarray(p, dtype=float), 1e-6, 1 - 1e-6)
        if self.calibration_kind == "platt" and self.calibrator is not None:
            return self.calibrator.predict_proba(safe_logit(p).reshape(-1, 1))[:, 1]
        if self.calibration_kind == "isotonic" and self.calibrator is not None:
            return self.calibrator.predict(p)
        return p

    def predict_proba(self, payloads: list[dict[str, Any]], numeric_df: pd.DataFrame) -> np.ndarray:
        raw = self.model.predict_proba(self._matrix(payloads, numeric_df))[:, 1]
        return self._calibrate(raw)


def _metrics(y: np.ndarray, p: np.ndarray) -> dict[str, float]:
    p = np.clip(np.asarray(p, dtype=float), 1e-6, 1 - 1e-6)
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


def _oof_text_model(
    payloads: list[dict[str, Any]],
    numeric_df: pd.DataFrame,
    y: np.ndarray,
    mode: str,
    classifier: str,
    include_numeric: bool,
    c_value: float,
    random_seed: int,
) -> tuple[np.ndarray, SparseTextModel]:
    folds = min(3, max(2, np.bincount(y).min()))
    skf = StratifiedKFold(n_splits=folds, shuffle=True, random_state=random_seed)
    oof = np.zeros(len(y), dtype=float)
    payloads_arr = np.asarray(payloads, dtype=object)
    for tr, va in skf.split(np.zeros(len(y)), y):
        model = SparseTextModel(
            mode=mode,
            classifier=classifier,
            include_numeric=include_numeric,
            c_value=c_value,
            random_seed=random_seed,
        )
        model.fit(list(payloads_arr[tr]), numeric_df.iloc[tr], y[tr])
        oof[va] = model.predict_proba(list(payloads_arr[va]), numeric_df.iloc[va])
    final = SparseTextModel(
        mode=mode,
        classifier=classifier,
        include_numeric=include_numeric,
        c_value=c_value,
        random_seed=random_seed,
    ).fit(payloads, numeric_df, y)
    return oof, final


def _best_alpha(y: np.ndarray, base: np.ndarray, candidate: np.ndarray) -> tuple[float, float]:
    best_alpha = 0.0
    best_loss = float("inf")
    for alpha in np.linspace(0.0, 1.0, 41):
        p = np.clip((1.0 - alpha) * base + alpha * candidate, 1e-6, 1 - 1e-6)
        loss = log_loss(y, p, labels=[0, 1])
        if loss < best_loss:
            best_loss = float(loss)
            best_alpha = float(alpha)
    return best_alpha, best_loss


def _fit_best_calibrator(y: np.ndarray, oof: np.ndarray) -> tuple[str, Any | None, np.ndarray, float]:
    p = np.clip(np.asarray(oof, dtype=float), 1e-6, 1 - 1e-6)
    candidates: list[tuple[str, Any | None, np.ndarray]] = [("none", None, p)]
    platt = LogisticRegression(C=1.0, max_iter=1000, random_state=42)
    platt.fit(safe_logit(p).reshape(-1, 1), y)
    candidates.append(("platt", platt, platt.predict_proba(safe_logit(p).reshape(-1, 1))[:, 1]))
    isotonic = IsotonicRegression(out_of_bounds="clip")
    isotonic.fit(p, y)
    candidates.append(("isotonic", isotonic, isotonic.predict(p)))
    best_kind = "none"
    best_model = None
    best_p = p
    best_loss = float("inf")
    for kind, calibrator, calibrated in candidates:
        calibrated = np.clip(calibrated, 1e-6, 1 - 1e-6)
        loss = log_loss(y, calibrated, labels=[0, 1])
        if loss < best_loss:
            best_kind = kind
            best_model = calibrator
            best_p = calibrated
            best_loss = float(loss)
    return best_kind, best_model, best_p, best_loss


def _apply_calibrator(kind: str, calibrator: Any | None, p: np.ndarray) -> np.ndarray:
    p = np.clip(np.asarray(p, dtype=float), 1e-6, 1 - 1e-6)
    if kind == "platt" and calibrator is not None:
        return calibrator.predict_proba(safe_logit(p).reshape(-1, 1))[:, 1]
    if kind == "isotonic" and calibrator is not None:
        return calibrator.predict(p)
    return p


def train_fast_text_evidence_model(train_limit: int = 1200, mode: str = "evidence_tables_safe") -> dict[str, Any]:
    cfg = load_config()
    seed = int(cfg["models"].get("random_seed", 42))
    examples = select_train_subset(load_iclr_examples(config=cfg), train_limit, seed)
    papers = load_public_papers(config=cfg)
    labels = load_test_labels(config=cfg)
    labeled = [paper for paper in papers if paper.paper_id in labels]
    artifacts = load_model_artifacts(cfg)
    artifacts.pop("fulltext_text_evidence_model", None)

    train_records, train_payloads = parsed_records(examples)
    test_records, test_payloads = parsed_records(labeled)
    X_train, train_records2 = build_fulltext_feature_frame(train_records, artifacts)
    X_test, test_records2 = build_fulltext_feature_frame(test_records, artifacts)
    id_to_payload_train = {_paper_id(r): p for r, p in zip(train_records, train_payloads)}
    id_to_payload_test = {_paper_id(r): p for r, p in zip(test_records, test_payloads)}
    train_payloads = [id_to_payload_train[_paper_id(r)] for r in train_records2]
    test_payloads = [id_to_payload_test[_paper_id(r)] for r in test_records2]
    train_records = train_records2
    test_records = test_records2
    y_train = np.asarray([1 if r.decision == "accept" else 0 for r in train_records], dtype=int)
    y_test = np.asarray([int(labels[r.paper_id]["accept_label"]) for r in test_records], dtype=int)
    base_train = X_train["base_p_accept"].to_numpy()
    base_test = X_test["base_p_accept"].to_numpy()
    train_texts = [payload_text(payload, mode) for payload in train_payloads]
    test_texts = [payload_text(payload, mode) for payload in test_payloads]

    folds = min(3, max(2, np.bincount(y_train).min()))
    skf = StratifiedKFold(n_splits=folds, shuffle=True, random_state=seed)
    oof = np.zeros(len(y_train), dtype=float)
    for fold_idx, (tr, va) in enumerate(skf.split(np.zeros(len(y_train)), y_train), start=1):
        vectorizer = TfidfVectorizer(
            max_features=30000,
            ngram_range=(1, 2),
            min_df=2,
            max_df=0.98,
            sublinear_tf=True,
            strip_accents="unicode",
            token_pattern=r"(?u)\b[\w][\w.-]{1,}\b",
        )
        scaler = StandardScaler()
        X_text_tr = vectorizer.fit_transform([train_texts[i] for i in tr])
        X_text_va = vectorizer.transform([train_texts[i] for i in va])
        X_num_tr = scaler.fit_transform(X_train.iloc[tr].values)
        X_num_va = scaler.transform(X_train.iloc[va].values)
        model = SGDClassifier(
            loss="log_loss",
            alpha=5e-5,
            penalty="elasticnet",
            l1_ratio=0.02,
            max_iter=30,
            tol=1e-3,
            class_weight="balanced",
            average=True,
            random_state=seed + fold_idx,
        )
        model.fit(hstack([X_text_tr, csr_matrix(X_num_tr)], format="csr"), y_train[tr])
        oof[va] = model.predict_proba(hstack([X_text_va, csr_matrix(X_num_va)], format="csr"))[:, 1]

    calibration_kind, calibrator, calibrated_oof, calibration_loss = _fit_best_calibrator(y_train, oof)
    blend_alpha, train_blend_loss = _best_alpha(y_train, base_train, calibrated_oof)

    vectorizer = TfidfVectorizer(
        max_features=30000,
        ngram_range=(1, 2),
        min_df=2,
        max_df=0.98,
        sublinear_tf=True,
        strip_accents="unicode",
        token_pattern=r"(?u)\b[\w][\w.-]{1,}\b",
    )
    scaler = StandardScaler()
    X_text_train = vectorizer.fit_transform(train_texts)
    X_text_test = vectorizer.transform(test_texts)
    X_num_train = scaler.fit_transform(X_train.values)
    X_num_test = scaler.transform(X_test.values)
    final_model = SGDClassifier(
        loss="log_loss",
        alpha=5e-5,
        penalty="elasticnet",
        l1_ratio=0.02,
        max_iter=30,
        tol=1e-3,
        class_weight="balanced",
        average=True,
        random_state=seed,
    )
    final_model.fit(hstack([X_text_train, csr_matrix(X_num_train)], format="csr"), y_train)
    raw_test = final_model.predict_proba(hstack([X_text_test, csr_matrix(X_num_test)], format="csr"))[:, 1]
    p_test = _apply_calibrator(calibration_kind, calibrator, raw_test)
    p_blend = np.clip((1.0 - blend_alpha) * base_test + blend_alpha * p_test, 1e-6, 1 - 1e-6)

    production_model = FastTextEvidenceProductionModel(
        mode=mode,
        vectorizer=vectorizer,
        scaler=scaler,
        model=final_model,
        numeric_columns=list(X_train.columns),
        blend_alpha=blend_alpha,
        calibration_kind=calibration_kind,
        calibrator=calibrator,
    )
    out_dir = ensure_dir(resolve_path(cfg, "model_dir"))
    joblib.dump(production_model, out_dir / "fulltext_text_evidence_model.pkl")
    metrics = {
        "mode": mode,
        "train_limit": train_limit,
        "num_train_parsed": len(train_records),
        "num_test_parsed": len(test_records),
        "calibration_kind": calibration_kind,
        "train_oof_raw": _metrics(y_train, oof),
        "train_oof_calibrated": _metrics(y_train, calibrated_oof),
        "train_calibrated_log_loss": calibration_loss,
        "blend_alpha_by_train_oof_logloss": blend_alpha,
        "train_blend_log_loss": train_blend_loss,
        "base_on_same_subset": _metrics(y_test, base_test),
        "text_evidence_on_test": _metrics(y_test, p_test),
        "blend_on_test": _metrics(y_test, p_blend),
    }
    dump_json(out_dir / "fast_text_evidence_metrics.json", metrics)
    dump_json(out_dir / f"fast_text_evidence_metrics_{mode}.json", metrics)
    pd.DataFrame(
        {
            "paper_id": [r.paper_id for r in test_records],
            "label": y_test,
            "base": base_test,
            "text_evidence": p_test,
            "text_evidence_blend": p_blend,
        }
    ).to_csv(out_dir / "fast_text_evidence_test_predictions.csv", index=False)
    return metrics


def run_advanced_fulltext_experiments(train_limit: int = 1200, include_diagnostics: bool = False) -> dict[str, Any]:
    cfg = load_config()
    seed = int(cfg["models"].get("random_seed", 42))
    examples = select_train_subset(load_iclr_examples(config=cfg), train_limit, seed)
    papers = load_public_papers(config=cfg)
    labels = load_test_labels(config=cfg)
    labeled = [paper for paper in papers if paper.paper_id in labels]
    artifacts = load_model_artifacts(cfg)

    train_records, train_payloads = parsed_records(examples)
    test_records, test_payloads = parsed_records(labeled)
    X_train, train_records2 = build_fulltext_feature_frame(train_records, artifacts)
    X_test, test_records2 = build_fulltext_feature_frame(test_records, artifacts)
    # Keep the feature-frame ordering authoritative.
    id_to_payload_train = {_paper_id(r): p for r, p in zip(train_records, train_payloads)}
    id_to_payload_test = {_paper_id(r): p for r, p in zip(test_records, test_payloads)}
    train_payloads = [id_to_payload_train[_paper_id(r)] for r in train_records2]
    test_payloads = [id_to_payload_test[_paper_id(r)] for r in test_records2]
    train_records = train_records2
    test_records = test_records2
    y_train = np.asarray([1 if r.decision == "accept" else 0 for r in train_records], dtype=int)
    y_test = np.asarray([int(labels[r.paper_id]["accept_label"]) for r in test_records], dtype=int)
    base_train = X_train["base_p_accept"].to_numpy()
    base_test = X_test["base_p_accept"].to_numpy()

    diagnostic_specs = [
        {"name": "tables_logreg_num", "mode": "tables", "classifier": "logreg", "include_numeric": True, "c": 0.5},
        {"name": "evidence_logreg_num", "mode": "evidence", "classifier": "logreg", "include_numeric": True, "c": 0.5},
        {"name": "sections_logreg_num", "mode": "sections", "classifier": "logreg", "include_numeric": True, "c": 0.5},
        {"name": "sections_logreg_text", "mode": "sections", "classifier": "logreg", "include_numeric": False, "c": 0.5},
        {"name": "full_sgd_num", "mode": "full", "classifier": "sgd", "include_numeric": True, "c": 1.0},
    ]
    safe_specs = [
        {"name": "tables_safe_sgd_num", "mode": "tables_safe", "classifier": "sgd", "include_numeric": True, "c": 0.5},
        {"name": "evidence_tables_safe_sgd_num", "mode": "evidence_tables_safe", "classifier": "sgd", "include_numeric": True, "c": 0.5},
        {"name": "evidence_safe_sgd_num", "mode": "evidence_safe", "classifier": "sgd", "include_numeric": True, "c": 0.5},
        {"name": "evidence_tables_safe_sgd_text", "mode": "evidence_tables_safe", "classifier": "sgd", "include_numeric": False, "c": 0.5},
    ]
    specs = (diagnostic_specs if include_diagnostics else []) + safe_specs
    results: dict[str, Any] = {
        "train_limit": train_limit,
        "include_diagnostics": include_diagnostics,
        "num_train_parsed": len(train_records),
        "num_test_parsed": len(test_records),
        "base": _metrics(y_test, base_test),
        "models": {},
    }
    oof_columns = [base_train]
    test_columns = [base_test]
    model_names = ["base"]
    final_models: dict[str, SparseTextModel] = {}
    best_safe_name = ""
    best_safe_model: SparseTextModel | None = None
    best_safe_alpha = 0.0
    best_safe_train_loss = float("inf")
    best_safe_oof_metrics: dict[str, float] = {}
    for spec in specs:
        oof, model = _oof_text_model(
            train_payloads,
            X_train,
            y_train,
            mode=spec["mode"],
            classifier=spec["classifier"],
            include_numeric=bool(spec["include_numeric"]),
            c_value=float(spec["c"]),
            random_seed=seed,
        )
        p_test = model.predict_proba(test_payloads, X_test)
        alpha, train_blend_loss = _best_alpha(y_train, base_train, oof)
        p_blend = np.clip((1.0 - alpha) * base_test + alpha * p_test, 1e-6, 1 - 1e-6)
        results["models"][spec["name"]] = {
            "pure": _metrics(y_test, p_test),
            "blend": _metrics(y_test, p_blend),
            "blend_alpha_by_train_oof_logloss": alpha,
            "train_oof": _metrics(y_train, oof),
            "train_blend_log_loss": train_blend_loss,
        }
        if str(spec["name"]).endswith("_safe_sgd_num") and train_blend_loss < best_safe_train_loss:
            best_safe_name = str(spec["name"])
            best_safe_model = model
            best_safe_alpha = alpha
            best_safe_train_loss = train_blend_loss
            best_safe_oof_metrics = _metrics(y_train, oof)
        oof_columns.append(oof)
        test_columns.append(p_test)
        model_names.append(spec["name"])
        final_models[spec["name"]] = model

    X_meta_train = np.column_stack([safe_logit(np.asarray(col)) for col in oof_columns])
    X_meta_test = np.column_stack([safe_logit(np.asarray(col)) for col in test_columns])
    meta_oof = np.zeros(len(y_train), dtype=float)
    skf = StratifiedKFold(n_splits=min(5, max(2, np.bincount(y_train).min())), shuffle=True, random_state=seed)
    for tr, va in skf.split(X_meta_train, y_train):
        meta = LogisticRegression(C=0.5, max_iter=1000, class_weight="balanced", random_state=seed)
        meta.fit(X_meta_train[tr], y_train[tr])
        meta_oof[va] = meta.predict_proba(X_meta_train[va])[:, 1]
    meta_final = LogisticRegression(C=0.5, max_iter=1000, class_weight="balanced", random_state=seed).fit(X_meta_train, y_train)
    p_meta_test = meta_final.predict_proba(X_meta_test)[:, 1]
    alpha, train_blend_loss = _best_alpha(y_train, base_train, meta_oof)
    p_meta_blend = np.clip((1 - alpha) * base_test + alpha * p_meta_test, 1e-6, 1 - 1e-6)
    results["meta_stack"] = {
        "model_names": model_names,
        "train_oof": _metrics(y_train, meta_oof),
        "pure": _metrics(y_test, p_meta_test),
        "blend": _metrics(y_test, p_meta_blend),
        "blend_alpha_by_train_oof_logloss": alpha,
        "train_blend_log_loss": train_blend_loss,
        "coefficients": {name: float(coef) for name, coef in zip(model_names, meta_final.coef_[0])},
        "intercept": float(meta_final.intercept_[0]),
    }

    out_dir = ensure_dir(resolve_path(cfg, "model_dir"))
    joblib.dump({"meta": meta_final, "models": final_models, "model_names": model_names}, out_dir / "advanced_fulltext_stack.pkl")
    if best_safe_model is not None:
        production_model = TextEvidenceProductionModel(
            text_model=best_safe_model,
            blend_alpha=best_safe_alpha,
            name=best_safe_name,
            train_oof_metrics=best_safe_oof_metrics,
        )
        joblib.dump(production_model, out_dir / "fulltext_text_evidence_model.pkl")
        results["selected_safe_production_model"] = {
            "name": best_safe_name,
            "blend_alpha_by_train_oof_logloss": best_safe_alpha,
            "train_blend_log_loss": best_safe_train_loss,
            "train_oof": best_safe_oof_metrics,
        }
    dump_json(out_dir / "advanced_fulltext_metrics.json", results)
    pd.DataFrame(
        {
            "paper_id": [r.paper_id for r in test_records],
            "label": y_test,
            "base": base_test,
            "advanced_meta": p_meta_test,
            "advanced_meta_blend": p_meta_blend,
        }
    ).to_csv(out_dir / "advanced_fulltext_test_predictions.csv", index=False)
    return results
