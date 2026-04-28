from __future__ import annotations

from typing import Any

import pandas as pd

from koala_strategy.llm.pseudo_review_panel import panel_features_for_text
from koala_strategy.paper.paper_features import extract_structured_features, feature_text_for_record
from koala_strategy.paper.pdf_parser import parsed_from_record


STRUCTURED_NUMERIC_COLUMNS = [
    "num_pages_estimate",
    "num_tokens",
    "num_sections",
    "has_abstract",
    "has_limitations_section",
    "has_reproducibility_statement",
    "num_tables",
    "num_figures",
    "has_ablation_keyword",
    "has_baseline_keyword",
    "has_sota_keyword",
    "has_error_bar_keyword",
    "has_confidence_interval_keyword",
    "has_statistical_significance_keyword",
    "num_dataset_mentions",
    "num_metric_mentions",
    "has_theorem",
    "has_lemma",
    "has_proposition",
    "has_proof",
    "num_equation_markers",
    "assumption_keyword_count",
    "has_github_url",
    "has_code_url",
    "has_appendix",
    "has_hyperparameter_details",
    "num_references",
    "recent_reference_ratio_estimate",
    "arxiv_reference_count",
    "domain_count",
    "keyword_count",
]


def structured_features_for_record(record: Any) -> dict[str, float]:
    if hasattr(record, "title"):
        title = getattr(record, "title", "")
        abstract = getattr(record, "abstract", "")
        full_text = getattr(record, "full_text", "")
        domains = getattr(record, "domains", []) or []
        meta = getattr(record, "metadata", {}) or {}
    else:
        title = record.get("title", "")
        abstract = record.get("abstract", "")
        full_text = record.get("full_text") or record.get("tldr", "")
        domains = record.get("domains", []) or []
        meta = record
    parsed = parsed_from_record(title, abstract, full_text)
    features = extract_structured_features(parsed)
    features["domain_count"] = len(domains)
    features["keyword_count"] = len(meta.get("keywords", []) or []) if isinstance(meta, dict) else 0
    return {k: float(features.get(k, 0.0) or 0.0) for k in STRUCTURED_NUMERIC_COLUMNS}


def structured_frame(records: list[Any]) -> pd.DataFrame:
    return pd.DataFrame([structured_features_for_record(record) for record in records]).fillna(0.0)


def pseudo_review_frame(records: list[Any]) -> pd.DataFrame:
    rows = []
    for record in records:
        if hasattr(record, "title"):
            title = getattr(record, "title", "")
            abstract = getattr(record, "abstract", "")
            full_text = getattr(record, "full_text", "")
        else:
            title = record.get("title", "")
            abstract = record.get("abstract", "")
            full_text = record.get("full_text") or record.get("tldr", "")
        rows.append(panel_features_for_text(title, abstract, full_text))
    return pd.DataFrame(rows).fillna(0.0)


def text_corpus(records: list[Any]) -> list[str]:
    return [feature_text_for_record(record) for record in records]
