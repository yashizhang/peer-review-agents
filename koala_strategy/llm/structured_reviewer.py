from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from koala_strategy.config import load_config, project_root, resolve_path
from koala_strategy.data.iclr_loader import read_table
from koala_strategy.llm.json_guard import extract_json_object
from koala_strategy.llm.providers import TextProvider, get_text_provider
from koala_strategy.utils import clamp, dump_json, ensure_dir, iter_jsonl, write_jsonl


PROMPT_VERSION = "self_review_structured_v1"
REVIEW_AXES = [
    "empirical_validation",
    "clarity_reproducibility",
    "robustness_generalization_compute",
    "technical_soundness",
    "novelty_positioning",
]


def _num(value: Any, default: float, lo: float, hi: float) -> float:
    try:
        return float(clamp(float(value), lo, hi))
    except (TypeError, ValueError):
        return default


def _processed_root(subset: str, processed_root: Path | None = None) -> Path:
    return processed_root or project_root() / "data" / "processed_papers" / subset


def _default_output_root(subset: str) -> Path:
    return project_root() / "data" / "structured_features" / subset


def iter_processed_paper_dirs(subset: str = "iclr26", processed_root: Path | None = None) -> list[Path]:
    root = _processed_root(subset, processed_root)
    if not root.exists():
        return []
    return sorted(path for path in root.iterdir() if path.is_dir())


def read_processed_paper_text(paper_dir: Path, max_chars: int = 60000) -> str:
    for name in ["model_text_v3.txt", "paper.md"]:
        path = paper_dir / name
        if path.exists():
            return path.read_text(encoding="utf-8")[:max_chars]
    raise FileNotFoundError(f"No processed text found under {paper_dir}")


def _load_iclr_metadata(config: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    cfg = config or load_config()
    dataset_dir = resolve_path(cfg, "koala_dataset_dir")
    rows: dict[str, dict[str, Any]] = {}
    for filename, split in [
        ("global_train.jsonl", "train"),
        ("global_test_public.jsonl", "test"),
        ("global_test_labels.jsonl", "test"),
    ]:
        path = dataset_dir / filename
        if not path.exists():
            continue
        for row in read_table(path):
            paper_id = str(row.get("paper_id") or "")
            if not paper_id:
                continue
            rows.setdefault(paper_id, {}).update(row)
            rows[paper_id]["split"] = split
    return rows


def _paper_id_from_dir(paper_dir: Path) -> str:
    try:
        meta_path = paper_dir / "dataset_meta.json"
        if meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if meta.get("paper_id"):
                return str(meta["paper_id"])
    except json.JSONDecodeError:
        pass
    return paper_dir.name


def build_self_review_prompt(paper_id: str, title: str, domains: list[str], paper_text: str) -> str:
    axes = ", ".join(REVIEW_AXES)
    return f"""You are an internal peer-review feature extractor for a machine-learning paper.

Use only the paper text below. Do not infer from venue status, decisions, official reviews, citations, authors, acknowledgements, or post-publication information.

Return ONLY one JSON object with this shape:
{{
  "axes": {{
    "empirical_validation": {{"score": 0.0, "risk": 0.0, "confidence": 0.0, "rationale": "...", "evidence": ["..."]}},
    "clarity_reproducibility": {{"score": 0.0, "risk": 0.0, "confidence": 0.0, "rationale": "...", "evidence": ["..."]}},
    "robustness_generalization_compute": {{"score": 0.0, "risk": 0.0, "confidence": 0.0, "rationale": "...", "evidence": ["..."]}},
    "technical_soundness": {{"score": 0.0, "risk": 0.0, "confidence": 0.0, "rationale": "...", "evidence": ["..."]}},
    "novelty_positioning": {{"score": 0.0, "risk": 0.0, "confidence": 0.0, "rationale": "...", "evidence": ["..."]}}
  }},
  "overall_accept_probability": 0.0,
  "strongest_accept_signal": "...",
  "strongest_reject_signal": "...",
  "short_rationale": "..."
}}

Axis scores are 0-10 where higher is stronger. Axis risks and confidence are 0-1.

Paper id: {paper_id}
Title: {title}
Domains: {", ".join(domains)}
Axes to evaluate: {axes}

Paper text:
{paper_text}
"""


def validate_self_review(data: dict[str, Any], paper_id: str, model: str) -> dict[str, Any]:
    raw_axes = data.get("axes") if isinstance(data.get("axes"), dict) else {}
    axes: dict[str, dict[str, Any]] = {}
    for axis in REVIEW_AXES:
        raw = raw_axes.get(axis) if isinstance(raw_axes.get(axis), dict) else {}
        evidence = raw.get("evidence", [])
        axes[axis] = {
            "score": _num(raw.get("score"), 5.0, 0.0, 10.0),
            "risk": _num(raw.get("risk"), 0.5, 0.0, 1.0),
            "confidence": _num(raw.get("confidence"), 0.5, 0.0, 1.0),
            "rationale": str(raw.get("rationale") or ""),
            "evidence": [str(item)[:500] for item in evidence[:5]] if isinstance(evidence, list) else [],
        }
    return {
        "paper_id": paper_id,
        "prompt_version": PROMPT_VERSION,
        "model": model,
        "axes": axes,
        "overall_accept_probability": _num(data.get("overall_accept_probability"), 0.5, 0.0, 1.0),
        "strongest_accept_signal": str(data.get("strongest_accept_signal") or ""),
        "strongest_reject_signal": str(data.get("strongest_reject_signal") or ""),
        "short_rationale": str(data.get("short_rationale") or ""),
    }


def extract_self_review_for_paper(
    paper_dir: Path,
    *,
    provider: TextProvider,
    cache_dir: Path,
    title: str = "",
    domains: list[str] | None = None,
    model: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    paper_id = _paper_id_from_dir(paper_dir)
    cache_path = ensure_dir(cache_dir) / f"{paper_id}.json"
    if cache_path.exists() and not force:
        return json.loads(cache_path.read_text(encoding="utf-8"))
    provider_model = model or getattr(provider, "model", None) or "unknown"
    prompt = build_self_review_prompt(
        paper_id=paper_id,
        title=title,
        domains=domains or [],
        paper_text=read_processed_paper_text(paper_dir),
    )
    raw = provider.generate(prompt, model=model, temperature=0.0)
    result = validate_self_review(extract_json_object(raw), paper_id=paper_id, model=str(provider_model))
    dump_json(cache_path, result)
    return result


def flatten_self_review_features(review: dict[str, Any]) -> dict[str, float]:
    features = {
        "self_overall_accept_probability": _num(review.get("overall_accept_probability"), 0.5, 0.0, 1.0),
    }
    axes = review.get("axes") if isinstance(review.get("axes"), dict) else {}
    for axis in REVIEW_AXES:
        raw = axes.get(axis) if isinstance(axes.get(axis), dict) else {}
        features[f"self_{axis}_score"] = _num(raw.get("score"), 5.0, 0.0, 10.0)
        features[f"self_{axis}_risk"] = _num(raw.get("risk"), 0.5, 0.0, 1.0)
        features[f"self_{axis}_confidence"] = _num(raw.get("confidence"), 0.5, 0.0, 1.0)
    return features


def extract_self_review_features(
    *,
    subset: str = "iclr26",
    processed_root: Path | None = None,
    output_path: Path | None = None,
    cache_dir: Path | None = None,
    limit: int | None = None,
    workers: int = 1,
    force: bool = False,
    provider: TextProvider | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = config or load_config()
    papers = iter_processed_paper_dirs(subset, processed_root)
    if limit is not None:
        papers = papers[:limit]
    output_root = _default_output_root(subset)
    output = output_path or output_root / "self_review_features.jsonl"
    cache = cache_dir or output_root / "self_review_cache"
    metadata = _load_iclr_metadata(cfg)
    text_provider = provider or get_text_provider(cfg)
    max_workers = max(1, int(workers))

    def process_paper(paper_dir: Path) -> dict[str, Any]:
        paper_id = _paper_id_from_dir(paper_dir)
        meta = metadata.get(paper_id, {})
        review = extract_self_review_for_paper(
            paper_dir,
            provider=text_provider,
            cache_dir=cache,
            title=str(meta.get("title") or paper_id),
            domains=[str(x) for x in (meta.get("domains") or [])],
            force=force,
        )
        row: dict[str, Any] = {"paper_id": paper_id}
        if meta.get("split"):
            row["split"] = str(meta["split"])
        if meta.get("accept_label") is not None:
            row["accept_label"] = int(meta["accept_label"])
        row.update(flatten_self_review_features(review))
        return row

    if max_workers == 1 or len(papers) <= 1:
        rows = [process_paper(paper_dir) for paper_dir in papers]
    else:
        rows: list[dict[str, Any] | None] = [None] * len(papers)
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(process_paper, paper_dir): idx for idx, paper_dir in enumerate(papers)}
            for future in as_completed(futures):
                rows[futures[future]] = future.result()
        rows = [row for row in rows if row is not None]
    write_jsonl(output, rows)
    summary = {
        "subset": subset,
        "num_papers": len(rows),
        "workers": max_workers,
        "output": str(output),
        "cache_dir": str(cache),
    }
    dump_json(output.with_suffix(".summary.json"), summary)
    return summary


def load_self_review_feature_rows(path: Path) -> list[dict[str, Any]]:
    return list(iter_jsonl(path))
