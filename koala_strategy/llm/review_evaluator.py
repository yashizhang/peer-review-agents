from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from koala_strategy.config import load_config, project_root, resolve_path
from koala_strategy.data.iclr_loader import read_table
from koala_strategy.llm.json_guard import extract_json_object
from koala_strategy.llm.providers import TextProvider, get_text_provider
from koala_strategy.llm.structured_reviewer import REVIEW_AXES, _num, flatten_self_review_features
from koala_strategy.utils import dump_json, ensure_dir, iter_jsonl, write_jsonl


PROMPT_VERSION = "review_evaluator_structured_v1"
REVIEW_TEXT_FIELDS = ["summary", "strengths", "weaknesses", "questions", "ethics"]


def _review_text(review: dict[str, Any], index: int) -> dict[str, str]:
    return {
        "review_id": str(review.get("note_id") or f"review_{index}"),
        "text": "\n\n".join(str(review.get(field) or "") for field in REVIEW_TEXT_FIELDS).strip(),
    }


def _load_train_rows(config: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    cfg = config or load_config()
    path = resolve_path(cfg, "koala_dataset_dir") / "global_train.jsonl"
    if not path.exists():
        return {}
    return {str(row["paper_id"]): row for row in read_table(path) if row.get("paper_id")}


def build_review_evaluator_prompt(
    paper_id: str,
    title: str,
    self_review: dict[str, Any],
    official_reviews: list[dict[str, Any]],
) -> str:
    sanitized_reviews = [_review_text(review, idx) for idx, review in enumerate(official_reviews)]
    payload = {
        "paper_id": paper_id,
        "title": title,
        "self_review_axes": self_review.get("axes", {}),
        "official_reviews_text_only": sanitized_reviews,
    }
    return f"""You are an internal review-quality evaluator for offline model features.

Use the self-review structured features and the text of external reviews. Ignore any numeric reviewer scores if they are absent; do not infer hidden scores.

Return ONLY one JSON object with this shape:
{{
  "review_reliabilities": [{{"review_id": "...", "reliability": 0.0, "rationale": "..."}}],
  "weighted_axes": {{
    "empirical_validation": {{"score": 0.0, "risk": 0.0, "confidence": 0.0}},
    "clarity_reproducibility": {{"score": 0.0, "risk": 0.0, "confidence": 0.0}},
    "robustness_generalization_compute": {{"score": 0.0, "risk": 0.0, "confidence": 0.0}},
    "technical_soundness": {{"score": 0.0, "risk": 0.0, "confidence": 0.0}},
    "novelty_positioning": {{"score": 0.0, "risk": 0.0, "confidence": 0.0}}
  }},
  "reliability_summary": {{"mean_reliability": 0.0, "review_disagreement": 0.0}},
  "summary": "..."
}}

Reliability is 0-1. Axis scores are 0-10 where higher is stronger. Axis risks and confidence are 0-1.

Input JSON:
{json.dumps(payload, ensure_ascii=False, sort_keys=True)}
"""


def _missing_external_result(paper_id: str, self_review: dict[str, Any]) -> dict[str, Any]:
    return {
        "paper_id": paper_id,
        "prompt_version": PROMPT_VERSION,
        "external_reviews_available": False,
        "num_external_reviews": 0,
        "review_reliabilities": [],
        "weighted_axes": {},
        "reliability_summary": {"mean_reliability": 0.0, "review_disagreement": 0.0},
        "gaps": _gap_axes(self_review, {}),
        "summary": "No official review text available for this paper.",
    }


def _validate_weighted_axes(data: dict[str, Any]) -> dict[str, dict[str, float]]:
    raw_axes = data.get("weighted_axes") if isinstance(data.get("weighted_axes"), dict) else {}
    axes: dict[str, dict[str, float]] = {}
    for axis in REVIEW_AXES:
        raw = raw_axes.get(axis) if isinstance(raw_axes.get(axis), dict) else {}
        axes[axis] = {
            "score": _num(raw.get("score"), 5.0, 0.0, 10.0),
            "risk": _num(raw.get("risk"), 0.5, 0.0, 1.0),
            "confidence": _num(raw.get("confidence"), 0.5, 0.0, 1.0),
        }
    return axes


def _gap_axes(self_review: dict[str, Any], weighted_axes: dict[str, dict[str, float]]) -> dict[str, dict[str, float]]:
    self_flat = flatten_self_review_features(self_review)
    gaps: dict[str, dict[str, float]] = {}
    for axis in REVIEW_AXES:
        external = weighted_axes.get(axis, {"score": 5.0, "risk": 0.5})
        gaps[axis] = {
            "score_gap": float(external["score"] - self_flat[f"self_{axis}_score"]),
            "risk_gap": float(external["risk"] - self_flat[f"self_{axis}_risk"]),
        }
    return gaps


def validate_external_review_evaluation(data: dict[str, Any], paper_id: str, self_review: dict[str, Any]) -> dict[str, Any]:
    reliabilities = []
    raw_reliabilities = data.get("review_reliabilities") if isinstance(data.get("review_reliabilities"), list) else []
    for idx, item in enumerate(raw_reliabilities):
        if not isinstance(item, dict):
            continue
        reliabilities.append(
            {
                "review_id": str(item.get("review_id") or f"review_{idx}"),
                "reliability": _num(item.get("reliability"), 0.5, 0.0, 1.0),
                "rationale": str(item.get("rationale") or ""),
            }
        )
    weighted_axes = _validate_weighted_axes(data)
    raw_summary = data.get("reliability_summary") if isinstance(data.get("reliability_summary"), dict) else {}
    return {
        "paper_id": paper_id,
        "prompt_version": PROMPT_VERSION,
        "external_reviews_available": True,
        "num_external_reviews": len(reliabilities),
        "review_reliabilities": reliabilities,
        "weighted_axes": weighted_axes,
        "reliability_summary": {
            "mean_reliability": _num(raw_summary.get("mean_reliability"), 0.5, 0.0, 1.0),
            "review_disagreement": _num(raw_summary.get("review_disagreement"), 0.0, 0.0, 1.0),
        },
        "gaps": _gap_axes(self_review, weighted_axes),
        "summary": str(data.get("summary") or ""),
    }


def evaluate_external_reviews_for_paper(
    *,
    paper_id: str,
    title: str,
    self_review: dict[str, Any],
    official_reviews: list[dict[str, Any]],
    provider: TextProvider,
    cache_dir: Path,
    model: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    cache_path = ensure_dir(cache_dir) / f"{paper_id}.json"
    if cache_path.exists() and not force:
        return json.loads(cache_path.read_text(encoding="utf-8"))
    if not official_reviews:
        return _missing_external_result(paper_id, self_review)
    prompt = build_review_evaluator_prompt(paper_id, title, self_review, official_reviews)
    raw = provider.generate(prompt, model=model, temperature=0.0)
    result = validate_external_review_evaluation(extract_json_object(raw), paper_id, self_review)
    dump_json(cache_path, result)
    return result


def flatten_external_review_features(result: dict[str, Any]) -> dict[str, float]:
    summary = result.get("reliability_summary") if isinstance(result.get("reliability_summary"), dict) else {}
    features = {
        "ext_reviews_available": 1.0 if result.get("external_reviews_available") else 0.0,
        "ext_num_external_reviews": float(result.get("num_external_reviews") or 0),
        "ext_mean_reliability": _num(summary.get("mean_reliability"), 0.0, 0.0, 1.0),
        "ext_review_disagreement": _num(summary.get("review_disagreement"), 0.0, 0.0, 1.0),
    }
    axes = result.get("weighted_axes") if isinstance(result.get("weighted_axes"), dict) else {}
    gaps = result.get("gaps") if isinstance(result.get("gaps"), dict) else {}
    for axis in REVIEW_AXES:
        raw_axis = axes.get(axis) if isinstance(axes.get(axis), dict) else {}
        raw_gap = gaps.get(axis) if isinstance(gaps.get(axis), dict) else {}
        features[f"ext_{axis}_score"] = _num(raw_axis.get("score"), 5.0, 0.0, 10.0)
        features[f"ext_{axis}_risk"] = _num(raw_axis.get("risk"), 0.5, 0.0, 1.0)
        features[f"ext_{axis}_confidence"] = _num(raw_axis.get("confidence"), 0.0, 0.0, 1.0)
        features[f"gap_{axis}_score"] = _num(raw_gap.get("score_gap"), 0.0, -10.0, 10.0)
        features[f"gap_{axis}_risk"] = _num(raw_gap.get("risk_gap"), 0.0, -1.0, 1.0)
    return features


def extract_review_evaluator_features(
    *,
    self_review_cache_dir: Path,
    subset: str = "iclr26",
    output_path: Path | None = None,
    cache_dir: Path | None = None,
    limit: int | None = None,
    force: bool = False,
    provider: TextProvider | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = config or load_config()
    train_rows = _load_train_rows(cfg)
    output_root = project_root() / "data" / "structured_features" / subset
    output = output_path or output_root / "review_evaluator_features.jsonl"
    cache = cache_dir or output_root / "review_evaluator_cache"
    text_provider = provider or get_text_provider(cfg)
    paper_ids = sorted(
        path.stem
        for path in self_review_cache_dir.glob("*.json")
        if path.stem in train_rows
    )
    if limit is not None:
        paper_ids = paper_ids[:limit]
    rows: list[dict[str, Any]] = []
    for paper_id in paper_ids:
        self_path = self_review_cache_dir / f"{paper_id}.json"
        train_row = train_rows[paper_id]
        self_review = json.loads(self_path.read_text(encoding="utf-8"))
        result = evaluate_external_reviews_for_paper(
            paper_id=paper_id,
            title=str(train_row.get("title") or paper_id),
            self_review=self_review,
            official_reviews=train_row.get("official_reviews") or [],
            provider=text_provider,
            cache_dir=cache,
            force=force,
        )
        row: dict[str, Any] = {"paper_id": paper_id}
        row.update(flatten_external_review_features(result))
        rows.append(row)
    write_jsonl(output, rows)
    summary = {"subset": subset, "num_papers": len(rows), "output": str(output), "cache_dir": str(cache)}
    dump_json(output.with_suffix(".summary.json"), summary)
    return summary


def load_external_review_feature_rows(path: Path) -> list[dict[str, Any]]:
    return list(iter_jsonl(path))
