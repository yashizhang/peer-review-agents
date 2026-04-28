from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, log_loss, roc_auc_score
from sklearn.model_selection import StratifiedKFold, train_test_split
from sklearn.preprocessing import StandardScaler

from koala_strategy.config import load_config, resolve_path
from koala_strategy.data.iclr_loader import load_iclr_examples, load_public_papers, load_test_labels
from koala_strategy.models.calibrate import classification_metrics, safe_logit
from koala_strategy.models.predict_paper_only import load_model_artifacts, predict_paper_only
from koala_strategy.paper.pdf_cache import batch_parse_pdf_records, pdf_cache_paths
from koala_strategy.llm.strong_judge import strong_judge_features
from koala_strategy.schemas import ParsedPaperText
from koala_strategy.utils import dump_json, ensure_dir, iter_jsonl


def _record_to_pdf_row(record: Any) -> dict[str, Any]:
    if hasattr(record, "paper_id"):
        return {
            "paper_id": record.paper_id,
            "title": record.title,
            "abstract": record.abstract,
            "openreview_pdf_url": record.metadata.get("openreview_pdf_url"),
            "pdf_url_from_note": record.metadata.get("pdf_url_from_note"),
        }
    return record


def _paper_id(record: Any) -> str:
    if hasattr(record, "paper_id"):
        return str(record.paper_id)
    return str(record.get("paper_id") or record.get("id"))


def parsed_payload_for_paper(paper_id: str) -> dict[str, Any] | None:
    _, parsed_path = pdf_cache_paths(paper_id)
    if not parsed_path.exists():
        return None
    payload = json.loads(parsed_path.read_text(encoding="utf-8"))
    return payload if payload.get("ok") else None


def feature_row_from_payload(payload: dict[str, Any]) -> dict[str, float]:
    parsed = ParsedPaperText.model_validate(payload)
    feats = strong_judge_features(parsed)
    feats["pdf_num_tokens_fulltext"] = float(len((parsed.full_text or "").split()))
    feats["pdf_num_sections_fulltext"] = float(len(parsed.sections))
    feats["pdf_num_references_fulltext"] = float(len(parsed.references))
    feats["pdf_num_figures_fulltext"] = float(len(parsed.figure_captions))
    feats["pdf_parser_warning_count"] = float(len(parsed.parser_warnings))
    return {k: float(v) for k, v in feats.items() if isinstance(v, (int, float, bool))}


def build_fulltext_feature_frame(records: list[Any], base_artifacts: dict[str, Any]) -> tuple[pd.DataFrame, list[Any]]:
    usable_records = []
    rows = []
    base_pred = predict_paper_only(records, base_artifacts)
    base_by_id = {_paper_id(record): idx for idx, record in enumerate(records)}
    for record in records:
        paper_id = _paper_id(record)
        payload = parsed_payload_for_paper(str(paper_id))
        if not payload:
            continue
        idx = base_by_id[str(paper_id)]
        row = feature_row_from_payload(payload)
        row["base_p_accept"] = float(base_pred["p_accept"][idx])
        row["base_uncertainty"] = float(base_pred["uncertainty"][idx])
        row["base_logit"] = float(safe_logit(row["base_p_accept"]))
        row["model_a_p"] = float(base_pred["p_model_a"][idx])
        row["model_b_p"] = float(base_pred["p_model_b"][idx])
        rows.append(row)
        usable_records.append(record)
    return pd.DataFrame(rows).fillna(0.0), usable_records


@dataclass
class FullTextEvidenceModel:
    model_type: str = "hgb"
    random_seed: int = 42

    def __post_init__(self) -> None:
        self.columns: list[str] = []
        self.scaler = StandardScaler()
        if self.model_type == "rf":
            self.model = RandomForestClassifier(
                n_estimators=300,
                max_depth=8,
                min_samples_leaf=6,
                class_weight="balanced_subsample",
                random_state=self.random_seed,
                n_jobs=-1,
            )
        elif self.model_type == "logreg":
            self.model = LogisticRegression(C=0.8, max_iter=1000, class_weight="balanced", random_state=self.random_seed)
        else:
            self.model = HistGradientBoostingClassifier(
                learning_rate=0.04,
                max_leaf_nodes=12,
                l2_regularization=0.1,
                max_iter=180,
                random_state=self.random_seed,
            )

    def _matrix(self, df: pd.DataFrame, fit: bool = False) -> np.ndarray:
        if fit:
            self.columns = list(df.columns)
            x = df.values
            return self.scaler.fit_transform(x) if self.model_type == "logreg" else x
        df = df.reindex(columns=self.columns, fill_value=0.0)
        x = df.values
        return self.scaler.transform(x) if self.model_type == "logreg" else x

    def fit(self, df: pd.DataFrame, y: np.ndarray) -> "FullTextEvidenceModel":
        self.model.fit(self._matrix(df, fit=True), y)
        return self

    def predict_proba(self, df: pd.DataFrame) -> np.ndarray:
        return self.model.predict_proba(self._matrix(df, fit=False))[:, 1]


def select_train_subset(examples: list[Any], limit: int, seed: int = 42) -> list[Any]:
    if limit <= 0 or limit >= len(examples):
        return examples
    y = np.asarray([1 if ex.decision == "accept" else 0 for ex in examples])
    _, subset = train_test_split(examples, test_size=limit, random_state=seed, stratify=y)
    return list(subset)


def parse_pdf_corpus(train_limit: int = 1200, test_all: bool = True, workers: int = 6, force: bool = False) -> dict[str, Any]:
    cfg = load_config()
    examples = select_train_subset(load_iclr_examples(config=cfg), train_limit, int(cfg["models"].get("random_seed", 42)))
    train_rows = [_record_to_pdf_row(ex) for ex in examples]
    test_rows = [_record_to_pdf_row(p) for p in load_public_papers(config=cfg)] if test_all else []
    train_results = batch_parse_pdf_records(train_rows, workers=workers, force=force, config=cfg)
    test_results = batch_parse_pdf_records(test_rows, workers=workers, force=force, config=cfg)
    summary = {
        "train_requested": len(train_rows),
        "train_ok": sum(bool(r.get("ok")) for r in train_results),
        "test_requested": len(test_rows),
        "test_ok": sum(bool(r.get("ok")) for r in test_results),
    }
    dump_json(Path("data/pdf_cache/parse_summary.json"), summary)
    return summary


def train_and_evaluate_fulltext(train_limit: int = 1200, model_type: str = "hgb") -> dict[str, Any]:
    cfg = load_config()
    examples = select_train_subset(load_iclr_examples(config=cfg), train_limit, int(cfg["models"].get("random_seed", 42)))
    papers = load_public_papers(config=cfg)
    labels = load_test_labels(config=cfg)
    labeled = [p for p in papers if p.paper_id in labels]
    artifacts = load_model_artifacts(cfg)
    X_train, train_records = build_fulltext_feature_frame(examples, artifacts)
    y_train = np.asarray([1 if r.decision == "accept" else 0 for r in train_records], dtype=int)
    X_test, test_records = build_fulltext_feature_frame(labeled, artifacts)
    y_test = np.asarray([int(labels[r.paper_id]["accept_label"]) for r in test_records], dtype=int)
    if len(X_train) < 50 or len(X_test) < 50:
        raise RuntimeError(f"Not enough parsed PDFs for experiment: train={len(X_train)}, test={len(X_test)}")

    random_seed = int(cfg["models"].get("random_seed", 42))
    folds = min(5, max(2, np.bincount(y_train).min()))
    oof = np.zeros(len(y_train), dtype=float)
    skf = StratifiedKFold(n_splits=folds, shuffle=True, random_state=random_seed)
    for tr, va in skf.split(X_train, y_train):
        fold_model = FullTextEvidenceModel(model_type=model_type, random_seed=random_seed).fit(X_train.iloc[tr], y_train[tr])
        oof[va] = fold_model.predict_proba(X_train.iloc[va])
    base_train = X_train["base_p_accept"].to_numpy()
    best_alpha = 0.0
    best_loss = float("inf")
    for alpha in np.linspace(0.0, 1.0, 21):
        p_oof = np.clip((1.0 - alpha) * base_train + alpha * oof, 1e-6, 1 - 1e-6)
        loss = log_loss(y_train, p_oof, labels=[0, 1])
        if loss < best_loss:
            best_loss = float(loss)
            best_alpha = float(alpha)

    model = FullTextEvidenceModel(model_type=model_type, random_seed=random_seed).fit(X_train, y_train)
    model.blend_alpha = best_alpha
    p_test = model.predict_proba(X_test)
    base_p = X_test["base_p_accept"].to_numpy()
    p_blend = np.clip((1.0 - best_alpha) * base_p + best_alpha * p_test, 1e-6, 1 - 1e-6)
    metrics = classification_metrics(y_test, p_test)
    metrics["blend_alpha_selected_by_train_oof_logloss"] = best_alpha
    metrics["train_oof_fulltext"] = classification_metrics(y_train, oof)
    metrics["train_oof_blend"] = classification_metrics(y_train, np.clip((1.0 - best_alpha) * base_train + best_alpha * oof, 1e-6, 1 - 1e-6))
    metrics["blend_on_test"] = classification_metrics(y_test, p_blend)
    metrics["base_on_same_subset"] = classification_metrics(y_test, base_p)
    metrics["num_train_parsed"] = len(X_train)
    metrics["num_test_parsed"] = len(X_test)
    metrics["model_type"] = model_type
    out_dir = ensure_dir(resolve_path(cfg, "model_dir"))
    joblib.dump(model, out_dir / f"fulltext_evidence_model_{model_type}.pkl")
    joblib.dump(model, out_dir / "fulltext_evidence_model.pkl")
    X_train.assign(label=y_train).to_csv(out_dir / "fulltext_train_features.csv", index=False)
    X_test.assign(label=y_test, paper_id=[r.paper_id for r in test_records], p_fulltext=p_test, p_blend=p_blend).to_csv(out_dir / f"fulltext_test_features_{model_type}.csv", index=False)
    X_train.assign(label=y_train, p_fulltext_oof=oof).to_csv(out_dir / f"fulltext_train_features_{model_type}.csv", index=False)
    dump_json(out_dir / "fulltext_evidence_metrics.json", metrics)
    return metrics
