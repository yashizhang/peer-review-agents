from __future__ import annotations

import re
import statistics
from typing import Any


TABLE_START_RE = re.compile(r"(?is)\bTable\s+([A-Za-z0-9.:-]+)\s*[:.]")
NEXT_FLOAT_RE = re.compile(r"(?is)\n\s*(?:Table|Figure|Fig\.|Algorithm)\s+[A-Za-z0-9.:-]+\s*[:.]")
METRIC_WORD_RE = re.compile(
    r"\b(accuracy|acc\.?|f1|auroc|auc|bleu|rouge|perplexity|ppl|success rate|win rate|mAP|IoU|WER|reward|return|regret|rmse|mae|loss|error)\b",
    re.I,
)
BASELINE_WORD_RE = re.compile(r"\b(baseline|sota|state-of-the-art|prior|existing|compare|comparison|vs\.?|ours|proposed)\b", re.I)
NUMBER_RE = re.compile(r"[-+]?\d+(?:\.\d+)?%?")


def extract_table_evidence_from_text(text: str, max_tables: int = 40) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    matches = list(TABLE_START_RE.finditer(text or ""))
    for idx, match in enumerate(matches):
        table_id = match.group(1)
        next_match = matches[idx + 1].start() if idx + 1 < len(matches) else len(text or "")
        window_end = min(next_match, match.start() + 4500)
        next_float = NEXT_FLOAT_RE.search(text or "", match.end(), window_end)
        if next_float and next_float.start() > match.start() + 800:
            window_end = next_float.start()
        chunk = " ".join((text or "")[match.start() : window_end].split())
        numbers = NUMBER_RE.findall(chunk)
        metrics = sorted(set(m.group(0).lower() for m in METRIC_WORD_RE.finditer(chunk)))
        has_baseline = bool(BASELINE_WORD_RE.search(chunk))
        numeric_density = len(numbers) / max(1, len(chunk.split()))
        evidence.append(
            {
                "table_id": str(table_id),
                "caption_or_context": chunk[:1500],
                "num_numeric_values": len(numbers),
                "numeric_density": numeric_density,
                "metrics": metrics[:12],
                "has_metric_keyword": bool(metrics),
                "has_baseline_keyword": has_baseline,
                "has_comparison_signal": has_baseline and bool(metrics or len(numbers) >= 4),
            }
        )
        if len(evidence) >= max_tables:
            break
    return evidence


def table_evidence_features(table_evidence: list[dict[str, Any]]) -> dict[str, float]:
    if not table_evidence:
        return {
            "pdf_num_tables_evidence": 0.0,
            "pdf_num_metric_tables": 0.0,
            "pdf_num_comparison_tables": 0.0,
            "pdf_table_numeric_value_count": 0.0,
            "pdf_table_numeric_density_mean": 0.0,
            "pdf_table_metric_diversity": 0.0,
        }
    all_metrics = set()
    densities = []
    for item in table_evidence:
        all_metrics.update(item.get("metrics", []) or [])
        densities.append(float(item.get("numeric_density", 0.0) or 0.0))
    return {
        "pdf_num_tables_evidence": float(len(table_evidence)),
        "pdf_num_metric_tables": float(sum(bool(x.get("has_metric_keyword")) for x in table_evidence)),
        "pdf_num_comparison_tables": float(sum(bool(x.get("has_comparison_signal")) for x in table_evidence)),
        "pdf_table_numeric_value_count": float(sum(int(x.get("num_numeric_values", 0) or 0) for x in table_evidence)),
        "pdf_table_numeric_density_mean": float(statistics.mean(densities)) if densities else 0.0,
        "pdf_table_metric_diversity": float(len(all_metrics)),
    }


def best_table_evidence(table_evidence: list[dict[str, Any]], limit: int = 3) -> list[dict[str, Any]]:
    def score(item: dict[str, Any]) -> float:
        context = str(item.get("caption_or_context", "")).lower()
        example_penalty = 1.8 if any(term in context for term in ["an example", "case study", "qualitative example"]) else 0.0
        return (
            1.4 * float(bool(item.get("has_comparison_signal")))
            + 1.2 * float(bool(item.get("has_metric_keyword")))
            + 0.03 * min(40, float(item.get("num_numeric_values", 0) or 0))
            + 0.2 * min(5, len(item.get("metrics", []) or []))
            - example_penalty
        )

    return sorted(table_evidence, key=score, reverse=True)[:limit]
