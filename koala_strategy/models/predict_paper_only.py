from __future__ import annotations

from pathlib import Path
from typing import Any

import joblib
import numpy as np

from koala_strategy.config import load_config, resolve_path
from koala_strategy.models.feature_schema import pseudo_review_frame
from koala_strategy.models.uncertainty import combine_uncertainty


def load_model_artifacts(config: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = config or load_config()
    model_dir = resolve_path(cfg, "model_dir")
    artifacts = {
        "model_a": joblib.load(model_dir / "model_a.pkl"),
        "model_b": joblib.load(model_dir / "model_b.pkl"),
        "stacker": joblib.load(model_dir / "stacker.pkl"),
    }
    model_c_path = model_dir / "model_c.pkl"
    if model_c_path.exists():
        artifacts["model_c"] = joblib.load(model_c_path)
    fulltext_path = model_dir / "fulltext_evidence_model.pkl"
    if fulltext_path.exists():
        artifacts["fulltext_evidence_model"] = joblib.load(fulltext_path)
    text_fulltext_path = model_dir / "fulltext_text_evidence_model.pkl"
    if text_fulltext_path.exists():
        artifacts["fulltext_text_evidence_model"] = joblib.load(text_fulltext_path)
    return artifacts


def predict_paper_only(records: list[Any], artifacts: dict[str, Any]) -> dict[str, np.ndarray]:
    model_a = artifacts["model_a"]
    model_b = artifacts["model_b"]
    stacker = artifacts["stacker"]
    p_a = model_a.predict_proba(records)
    p_b = model_b.predict_proba(records)
    panel_df = pseudo_review_frame(records)
    panel_disagreement = panel_df.get("panel_disagreement", 0.0).to_numpy(dtype=float)
    p_final = stacker.predict(p_a, p_b, panel_disagreement)
    uncertainty = np.asarray([combine_uncertainty(a, b, d) for a, b, d in zip(p_a, p_b, panel_disagreement)])
    return {
        "p_model_a": p_a,
        "p_model_b": p_b,
        "p_accept": p_final,
        "uncertainty": uncertainty,
        "panel_disagreement": panel_disagreement,
    }
