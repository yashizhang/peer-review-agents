from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import StratifiedKFold
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from koala_strategy.config import load_config, resolve_path
from koala_strategy.models.calibrate import classification_metrics
from koala_strategy.utils import dump_json, ensure_dir, iter_jsonl


EXCLUDE_COLUMNS = {"paper_id", "split", "accept_label", "decision", "decision_label"}
ENSEMBLE_STEP = 0.05
ENSEMBLE_OBJECTIVE = "log_loss"


def load_structured_feature_rows(self_features_path: Path, external_features_path: Path | None = None) -> list[dict[str, Any]]:
    external_by_id: dict[str, dict[str, Any]] = {}
    if external_features_path and external_features_path.exists():
        external_by_id = {str(row["paper_id"]): row for row in iter_jsonl(external_features_path) if row.get("paper_id")}
    rows: list[dict[str, Any]] = []
    for row in iter_jsonl(self_features_path):
        paper_id = str(row.get("paper_id") or "")
        merged = dict(row)
        if paper_id in external_by_id:
            for key, value in external_by_id[paper_id].items():
                if key != "paper_id":
                    merged[key] = value
        rows.append(merged)
    return rows


def _feature_frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    frame_rows: list[dict[str, float]] = []
    for row in rows:
        out: dict[str, float] = {}
        for key, value in row.items():
            if key in EXCLUDE_COLUMNS:
                continue
            try:
                out[key] = float(value)
            except (TypeError, ValueError):
                continue
        frame_rows.append(out)
    return pd.DataFrame(frame_rows).fillna(0.0)


def _labels(rows: list[dict[str, Any]]) -> np.ndarray:
    missing = [row.get("paper_id", idx) for idx, row in enumerate(rows) if row.get("accept_label") is None]
    if missing:
        raise ValueError(f"Missing accept_label for {len(missing)} structured feature rows.")
    return np.asarray([int(row["accept_label"]) for row in rows], dtype=int)


def _train_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not any("split" in row for row in rows):
        return rows
    return [row for row in rows if row.get("split") == "train"]


def _splitter(y: np.ndarray, n_folds: int, random_seed: int) -> StratifiedKFold:
    min_class = int(np.bincount(y).min()) if len(set(y.tolist())) > 1 else 1
    splits = min(int(n_folds), min_class)
    if splits < 2:
        raise ValueError("Need at least two examples per class for OOF structured model training.")
    return StratifiedKFold(n_splits=splits, shuffle=True, random_state=random_seed)


def _train_logistic(X: pd.DataFrame, y: np.ndarray, n_folds: int, random_seed: int) -> tuple[Any, np.ndarray]:
    oof = np.zeros(len(y), dtype=float)
    splitter = _splitter(y, n_folds, random_seed)
    for train_idx, valid_idx in splitter.split(X, y):
        fold_model = make_pipeline(
            StandardScaler(),
            LogisticRegression(C=0.7, max_iter=1000, random_state=random_seed),
        )
        fold_model.fit(X.iloc[train_idx], y[train_idx])
        oof[valid_idx] = fold_model.predict_proba(X.iloc[valid_idx])[:, 1]
    model = make_pipeline(
        StandardScaler(),
        LogisticRegression(C=0.7, max_iter=1000, random_state=random_seed),
    )
    model.fit(X, y)
    return model, oof


def _train_lightgbm(X: pd.DataFrame, y: np.ndarray, n_folds: int, random_seed: int) -> tuple[Any, np.ndarray] | None:
    if importlib.util.find_spec("lightgbm") is None:
        return None
    from lightgbm import LGBMClassifier  # type: ignore

    oof = np.zeros(len(y), dtype=float)
    splitter = _splitter(y, n_folds, random_seed)
    for train_idx, valid_idx in splitter.split(X, y):
        fold_model = LGBMClassifier(
            n_estimators=300,
            learning_rate=0.03,
            num_leaves=15,
            max_depth=3,
            min_child_samples=30,
            reg_lambda=10.0,
            feature_fraction=0.8,
            bagging_fraction=0.8,
            random_state=random_seed,
            verbose=-1,
        )
        fold_model.fit(X.iloc[train_idx], y[train_idx])
        oof[valid_idx] = fold_model.predict_proba(X.iloc[valid_idx])[:, 1]
    model = LGBMClassifier(
        n_estimators=300,
        learning_rate=0.03,
        num_leaves=15,
        max_depth=3,
        min_child_samples=30,
        reg_lambda=10.0,
        feature_fraction=0.8,
        bagging_fraction=0.8,
        random_state=random_seed,
        verbose=-1,
    )
    model.fit(X, y)
    return model, oof


def _train_xgboost(X: pd.DataFrame, y: np.ndarray, n_folds: int, random_seed: int) -> tuple[Any, np.ndarray] | None:
    if importlib.util.find_spec("xgboost") is None:
        return None
    from xgboost import XGBClassifier  # type: ignore

    oof = np.zeros(len(y), dtype=float)
    splitter = _splitter(y, n_folds, random_seed)
    for train_idx, valid_idx in splitter.split(X, y):
        fold_model = XGBClassifier(
            n_estimators=300,
            learning_rate=0.03,
            max_depth=3,
            subsample=0.8,
            colsample_bytree=0.8,
            reg_lambda=10.0,
            eval_metric="logloss",
            random_state=random_seed,
        )
        fold_model.fit(X.iloc[train_idx], y[train_idx])
        oof[valid_idx] = fold_model.predict_proba(X.iloc[valid_idx])[:, 1]
    model = XGBClassifier(
        n_estimators=300,
        learning_rate=0.03,
        max_depth=3,
        subsample=0.8,
        colsample_bytree=0.8,
        reg_lambda=10.0,
        eval_metric="logloss",
        random_state=random_seed,
    )
    model.fit(X, y)
    return model, oof


def _calibrated_metrics(y: np.ndarray, oof: np.ndarray) -> tuple[IsotonicRegression, np.ndarray, dict[str, Any]]:
    raw_metrics = classification_metrics(y, oof)
    calibrator = IsotonicRegression(out_of_bounds="clip")
    calibrator.fit(oof, y)
    calibrated = np.asarray(calibrator.predict(oof), dtype=float)
    metrics = classification_metrics(y, calibrated)
    metrics["raw_metrics"] = raw_metrics
    metrics["raw_oof_mean"] = float(np.mean(oof))
    metrics["calibrated_oof_mean"] = float(np.mean(calibrated))
    return calibrator, calibrated, metrics


def _evaluate_weight_objective(
    y: np.ndarray,
    p: np.ndarray,
    objective: str,
) -> tuple[float, bool]:
    objective = objective.lower()
    metrics = classification_metrics(y, p)
    if objective == "log_loss":
        return float(metrics["log_loss"]), True
    if objective == "brier":
        return float(metrics["brier"]), True
    if objective == "auroc":
        return float(metrics["auroc"]), False
    if objective == "auprc":
        return float(metrics["auprc"]), False
    if objective == "top_27_precision" or objective == "top_k_precision" or objective == "top_27_percent_precision":
        return float(metrics["top_27_percent_precision"]), False
    raise ValueError(f"Unknown ensemble objective: {objective}")


def _objective_is_better(score: float, best_score: float, lower_is_better: bool) -> bool:
    if lower_is_better:
        return score < best_score
    return score > best_score


def _search_weights(
    oofs: np.ndarray,
    y: np.ndarray,
    objective: str,
    step: float = ENSEMBLE_STEP,
) -> tuple[np.ndarray, float, bool]:
    if oofs.ndim != 2:
        raise ValueError("OOF prediction matrix must be 2D.")
    if oofs.shape[0] != len(y):
        raise ValueError("OOF prediction rows must match label length.")
    if not (0 < step <= 1):
        raise ValueError("Weight grid step must be in (0, 1].")
    num_models = oofs.shape[1]
    if num_models == 0:
        raise ValueError("Need at least one available base model to build ensemble.")
    if num_models == 1:
        p = oofs[:, 0].copy()
        score, lower_is_better = _evaluate_weight_objective(y, p, objective)
        return np.array([1.0], dtype=float), float(score), lower_is_better
    num_steps = int(round(1.0 / step))
    if num_steps <= 0:
        raise ValueError("Invalid grid step for ensemble weights.")
    best_weights = None
    best_score = 0.0
    lower_is_better = True
    for i in range(num_steps + 1):
        w0 = i / num_steps
        if num_models == 2:
            weights = np.array([w0, 1.0 - w0], dtype=float)
            p = oofs @ weights
            score, lowers = _evaluate_weight_objective(y, p, objective)
            if best_weights is None:
                best_weights = weights
                best_score = score
                lower_is_better = lowers
                continue
            if _objective_is_better(score, best_score, lower_is_better):
                best_weights = weights
                best_score = score
            continue
        for j in range(num_steps + 1 - i):
            w1 = j / num_steps
            w2 = 1.0 - w0 - w1
            if w2 < -1e-9:
                continue
            weights = np.array([w0, w1, w2], dtype=float)
            p = oofs @ weights
            score, lowers = _evaluate_weight_objective(y, p, objective)
            if best_weights is None:
                best_weights = weights
                best_score = score
                lower_is_better = lowers
                continue
            if _objective_is_better(score, best_score, lower_is_better):
                best_weights = weights
                best_score = score
    if best_weights is None:
        raise RuntimeError("Failed to find ensemble weights.")
    best_weights = np.clip(best_weights, 0.0, 1.0)
    best_weights /= float(best_weights.sum()) if best_weights.sum() > 0 else 1.0
    return best_weights, best_score, lower_is_better


def train_structured_verdict_models(
    rows: list[dict[str, Any]],
    *,
    output_dir: Path,
    n_folds: int = 3,
    random_seed: int = 42,
    ensemble_objective: str = ENSEMBLE_OBJECTIVE,
    ensemble_grid_step: float = ENSEMBLE_STEP,
    feature_mode: str = "self_only",
) -> dict[str, Any]:
    if not rows:
        raise ValueError("No structured feature rows provided.")
    training_rows = _train_rows(rows)
    if not training_rows:
        raise ValueError("No train split structured feature rows provided.")
    X = _feature_frame(training_rows)
    y = _labels(training_rows)
    if X.empty:
        raise ValueError("No numeric structured feature columns found.")
    out_dir = ensure_dir(output_dir)
    result: dict[str, Any] = {
        "num_examples": len(training_rows),
        "num_total_rows": len(rows),
        "num_accept": int(y.sum()),
        "num_reject": int(len(y) - y.sum()),
        "feature_columns": list(X.columns),
        "feature_mode": feature_mode,
        "models": {},
    }
    raw_oofs: dict[str, np.ndarray] = {}
    base_artifacts: dict[str, Any] = {}
    trainers = {
        "logistic": _train_logistic,
        "lightgbm": _train_lightgbm,
        "xgboost": _train_xgboost,
    }
    for name, trainer in trainers.items():
        trained = trainer(X, y, n_folds, random_seed)
        if trained is None:
            result["models"][name] = {"available": False}
            base_artifacts[name] = {"available": False}
            continue
        model, oof = trained
        calibrator, calibrated, metrics = _calibrated_metrics(y, oof)
        joblib.dump(
            {"model": model, "calibrator": calibrator, "feature_columns": list(X.columns)},
            out_dir / f"structured_{name}.pkl",
        )
        np.save(out_dir / f"structured_{name}_raw_oof_predictions.npy", oof)
        np.save(out_dir / f"structured_{name}_calibrated_oof_predictions.npy", calibrated)
        np.save(out_dir / f"structured_{name}_oof_predictions.npy", calibrated)
        raw_oofs[name] = oof
        base_artifacts[name] = {
            "available": True,
            "model": model,
            "calibrator": calibrator,
            "feature_columns": list(X.columns),
        }
        result["models"][name] = {"available": True, "metrics": metrics}

    available_names = [name for name in ("logistic", "lightgbm", "xgboost") if name in raw_oofs]
    if not available_names:
        raise ValueError("No structured base models are available.")
    oof_matrix = np.column_stack([raw_oofs[name] for name in available_names])
    raw_weights, best_objective_score, lower_is_better = _search_weights(
        oof_matrix,
        y,
        objective=ensemble_objective,
        step=ensemble_grid_step,
    )
    ensemble_raw_oof = oof_matrix @ raw_weights
    ensemble_calibrator = IsotonicRegression(out_of_bounds="clip")
    ensemble_calibrator.fit(ensemble_raw_oof, y)
    ensemble_calibrated = np.asarray(ensemble_calibrator.predict(ensemble_raw_oof), dtype=float)
    raw_ensemble_metrics = classification_metrics(y, ensemble_raw_oof)
    calibrated_ensemble_metrics = classification_metrics(y, ensemble_calibrated)
    weight_map = {name: float(w) for name, w in zip(available_names, raw_weights)}
    ensemble_bundle = {
        "base_models": base_artifacts,
        "weights": weight_map,
        "calibrator": ensemble_calibrator,
        "feature_columns": list(X.columns),
        "feature_mode": feature_mode,
        "objective": ensemble_objective,
        "weight_grid_step": float(ensemble_grid_step),
        "lower_is_better": bool(lower_is_better),
        "objective_score": float(best_objective_score),
    }
    joblib.dump(ensemble_bundle, out_dir / "structured_ensemble_weighted.pkl")
    np.save(out_dir / "structured_ensemble_weighted_raw_oof_predictions.npy", ensemble_raw_oof)
    np.save(out_dir / "structured_ensemble_weighted_calibrated_oof_predictions.npy", ensemble_calibrated)
    dump_json(
        out_dir / "structured_ensemble_metrics.json",
        {
            "feature_mode": feature_mode,
            "available_models": available_names,
            "weights": weight_map,
            "objective": ensemble_objective,
            "weight_grid_step": float(ensemble_grid_step),
            "raw_metrics": raw_ensemble_metrics,
            "calibrated_metrics": calibrated_ensemble_metrics,
        },
    )
    result["ensemble"] = {
        "available_models": available_names,
        "weights": weight_map,
        "objective": ensemble_objective,
        "weight_grid_step": float(ensemble_grid_step),
        "raw_metrics": raw_ensemble_metrics,
        "calibrated_metrics": calibrated_ensemble_metrics,
        "raw_oof_mean": float(np.mean(ensemble_raw_oof)),
        "calibrated_oof_mean": float(np.mean(ensemble_calibrated)),
    }

    dump_json(out_dir / "structured_feature_schema.json", {"feature_columns": list(X.columns)})
    dump_json(out_dir / "structured_model_metrics.json", result)
    return result


def train_structured_verdict_model_from_files(
    *,
    self_features_path: Path,
    external_features_path: Path | None = None,
    output_dir: Path | None = None,
    n_folds: int | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = config or load_config()
    rows = load_structured_feature_rows(self_features_path, external_features_path)
    out_dir = output_dir or resolve_path(cfg, "model_dir") / "structured_verdict"
    ensemble_cfg = cfg.get("models", {}).get("structured_ensemble", {})
    return train_structured_verdict_models(
        rows,
        output_dir=out_dir,
        n_folds=n_folds or int(cfg.get("models", {}).get("n_folds", 3)),
        random_seed=int(cfg.get("models", {}).get("random_seed", 42)),
        ensemble_objective=str(ensemble_cfg.get("objective", ENSEMBLE_OBJECTIVE)),
        ensemble_grid_step=float(ensemble_cfg.get("weight_grid_step", ENSEMBLE_STEP)),
        feature_mode="self_plus_external" if external_features_path is not None else "self_only",
    )
