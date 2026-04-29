#!/usr/bin/env python3
"""Build category-level review priors from non-eval ICLR 2026 official reviews."""

from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

AXIS_HINTS = {
    "evidence_completeness": (
        "ablation",
        "baseline",
        "benchmark",
        "compare",
        "comparison",
        "dataset",
        "evaluation",
        "experiment",
        "metric",
        "seed",
        "significance",
        "statistical",
        "variance",
    ),
    "clarity_reproducibility": (
        "algorithm",
        "clarity",
        "detail",
        "hyperparameter",
        "implementation",
        "notation",
        "presentation",
        "reproduc",
        "self-contained",
        "typo",
    ),
    "practical_scope": (
        "compute",
        "cost",
        "efficien",
        "failure mode",
        "generaliz",
        "latency",
        "memory",
        "ood",
        "robust",
        "runtime",
        "scale",
        "scalab",
    ),
    "technical_soundness": (
        "assumption",
        "causal",
        "claim",
        "confound",
        "convergence",
        "deriv",
        "justif",
        "leakage",
        "objective",
        "proof",
        "sound",
        "theorem",
    ),
    "novelty_positioning": (
        "contribution",
        "incremental",
        "missing related",
        "novel",
        "novelty",
        "original",
        "position",
        "prior work",
        "related work",
    ),
}
FIELD_DEFAULT_AXES = {
    "summary": ("technical_soundness", "novelty_positioning"),
    "strengths": ("evidence_completeness", "technical_soundness"),
    "weaknesses": ("evidence_completeness", "technical_soundness"),
    "questions": ("clarity_reproducibility", "evidence_completeness"),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--reviews-path",
        type=Path,
        default=Path("/network/scratch/z/zhangya/shared_resource_perturbflow/iclr2026_openreview/reviews.jsonl"),
    )
    parser.add_argument(
        "--papers-path",
        type=Path,
        default=Path("/network/scratch/z/zhangya/shared_resource_perturbflow/iclr2026_openreview/papers.jsonl"),
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=REPO_ROOT / "experimental/results/axis_panel_master_backtest_iclr2026_100/input_manifest.jsonl",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=REPO_ROOT / "experimental/artifacts/axis_panel_separate",
    )
    return parser.parse_args()


def load_eval_forum_ids(path: Path) -> set[str]:
    return {
        json.loads(line)["forum_id"]
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }


def load_papers(path: Path) -> dict[str, dict]:
    index = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            index[row["id"]] = {
                "title": row.get("title") or "",
                "status": row.get("status"),
                "categories": row.get("koala_or_fallback_categories") or [],
            }
    return index


def iter_official_reviews(reviews_path: Path, papers: dict[str, dict], excluded_forum_ids: set[str]):
    with reviews_path.open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            if row.get("kind") != "official_review":
                continue
            forum_id = row.get("paper_id") or row.get("forum")
            if not forum_id or forum_id in excluded_forum_ids:
                continue
            paper = papers.get(forum_id)
            if not paper:
                continue
            content = row.get("content") or {}
            yield {
                "forum_id": forum_id,
                "title": paper["title"],
                "status": paper.get("status"),
                "categories": paper.get("categories") or [],
                "rating": content.get("rating"),
                "confidence": content.get("confidence"),
                "summary": (content.get("summary") or "").strip(),
                "strengths": (content.get("strengths") or "").strip(),
                "weaknesses": (content.get("weaknesses") or "").strip(),
                "questions": (content.get("questions") or "").strip(),
            }


def split_lines(text: str) -> list[str]:
    lines = []
    for raw in text.splitlines():
        cleaned = raw.strip().lstrip("-").lstrip("*").strip()
        if cleaned:
            lines.append(cleaned)
    return lines or ([text.strip()] if text.strip() else [])


def infer_axes(text: str, fallback_axes: tuple[str, ...]) -> list[str]:
    lowered = text.lower()
    matches = [axis for axis, hints in AXIS_HINTS.items() if any(hint in lowered for hint in hints)]
    return matches or list(fallback_axes)


def confidence_bucket(value: int | float | None) -> str:
    if value is None:
        return "unknown"
    numeric = float(value)
    if numeric <= 2.0:
        return "low"
    if numeric <= 3.0:
        return "medium"
    return "high"


def numeric_summary(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"mean": None, "median": None, "stdev": None}
    return {
        "mean": round(sum(values) / len(values), 4),
        "median": round(statistics.median(values), 4),
        "stdev": round(statistics.pstdev(values), 4) if len(values) > 1 else 0.0,
    }


def build_priors(reviews_path: Path, papers_path: Path, manifest: Path) -> dict:
    excluded_forum_ids = load_eval_forum_ids(manifest)
    papers = load_papers(papers_path)
    categories: dict[str, dict] = defaultdict(
        lambda: {
            "paper_ids": set(),
            "review_count": 0,
            "ratings": [],
            "confidence_counts": Counter(),
            "axis_counts": Counter(),
            "low_rating_axis_counts": Counter(),
            "high_rating_axis_counts": Counter(),
        }
    )
    admitted_reviews = 0
    status_counts = Counter()
    for review in iter_official_reviews(reviews_path, papers, excluded_forum_ids):
        admitted_reviews += 1
        status_counts[review.get("status") or "unknown"] += 1
        rating = review.get("rating")
        confidence = review.get("confidence")
        review_axes = []
        for field, default_axes in FIELD_DEFAULT_AXES.items():
            for snippet in split_lines(review.get(field) or ""):
                review_axes.extend(infer_axes(snippet, fallback_axes=default_axes))
        unique_axes = set(review_axes)
        for category in review["categories"] or ["uncategorized"]:
            bucket = categories[category]
            bucket["paper_ids"].add(review["forum_id"])
            bucket["review_count"] += 1
            if isinstance(rating, (int, float)):
                bucket["ratings"].append(float(rating))
            bucket["confidence_counts"][confidence_bucket(confidence)] += 1
            bucket["axis_counts"].update(unique_axes)
            if isinstance(rating, (int, float)) and float(rating) <= 4.0:
                bucket["low_rating_axis_counts"].update(unique_axes)
            if isinstance(rating, (int, float)) and float(rating) >= 7.0:
                bucket["high_rating_axis_counts"].update(unique_axes)

    rendered_categories = {}
    for category, bucket in categories.items():
        review_count = bucket["review_count"]
        ratings = bucket["ratings"]
        low_axes = [axis for axis, _ in bucket["low_rating_axis_counts"].most_common(3)]
        high_axes = [axis for axis, _ in bucket["high_rating_axis_counts"].most_common(3)]
        rendered_categories[category] = {
            "paper_count": len(bucket["paper_ids"]),
            "review_count": review_count,
            "score_summary": numeric_summary(ratings),
            "score_histogram": {str(int(score)): ratings.count(score) for score in sorted(set(ratings))},
            "confidence_distribution": dict(bucket["confidence_counts"]),
            "axis_concern_frequencies": {
                axis: round(bucket["axis_counts"][axis] / review_count, 4) if review_count else 0.0
                for axis in AXIS_HINTS
            },
            "typical_severity_patterns": [
                f"Low-rated reviews often combine: {', '.join(low_axes) or 'no dominant axis'}",
                f"Higher-rated reviews more often emphasize: {', '.join(high_axes) or 'no dominant axis'}",
                f"Mean rating prior: {numeric_summary(ratings)['mean']}",
            ],
            "calibration_priors": [
                f"Median rating prior: {numeric_summary(ratings)['median']}",
                f"Confidence prior: {dict(bucket['confidence_counts'])}",
                f"Most frequent concern axes: {', '.join(axis for axis, _ in bucket['axis_counts'].most_common(3)) or 'none'}",
            ],
        }
    return {
        "artifact_type": "axis_panel_separate_priors",
        "source": {
            "reviews_path": str(reviews_path),
            "papers_path": str(papers_path),
            "manifest": str(manifest),
        },
        "excluded_eval_forum_ids": sorted(excluded_forum_ids),
        "excluded_eval_forum_count": len(excluded_forum_ids),
        "official_reviews_admitted": admitted_reviews,
        "source_status_counts": dict(sorted(status_counts.items())),
        "categories": rendered_categories,
    }


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    priors = build_priors(
        reviews_path=args.reviews_path,
        papers_path=args.papers_path,
        manifest=args.manifest,
    )
    output_path = args.output_dir / "review_priors_by_category.json"
    output_path.write_text(json.dumps(priors, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"artifact": str(output_path), "categories": len(priors["categories"]), "official_reviews": priors["official_reviews_admitted"]}, indent=2))


if __name__ == "__main__":
    main()
