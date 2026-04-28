from __future__ import annotations

import math
import re
from typing import Any

from koala_strategy.paper.paper_features import extract_structured_features
from koala_strategy.paper.table_evidence import table_evidence_features
from koala_strategy.schemas import ParsedPaperText
from koala_strategy.utils import clamp


NEGATIVE_TERMS = [
    "limitation",
    "limitations",
    "however",
    "fail",
    "failure",
    "sensitive",
    "lack",
    "missing",
    "not compare",
    "future work",
]
POSITIVE_TERMS = [
    "state-of-the-art",
    "outperform",
    "improve",
    "significant improvement",
    "novel",
    "theorem",
    "guarantee",
    "open-source",
]


def _count_terms(text: str, terms: list[str]) -> int:
    lower = text.lower()
    return sum(lower.count(term) for term in terms)


def strong_judge_features(parsed: ParsedPaperText) -> dict[str, float]:
    text = parsed.full_text or ""
    feats = extract_structured_features(parsed)
    table_feats = table_evidence_features(parsed.table_evidence)
    pos = _count_terms(text, POSITIVE_TERMS)
    neg = _count_terms(text, NEGATIVE_TERMS)
    refs = feats.get("num_references", 0)
    pages = feats.get("num_pages_estimate", 0)
    empirical_depth = (
        0.7 * feats.get("has_baseline_keyword", 0)
        + 0.7 * feats.get("has_ablation_keyword", 0)
        + 0.4 * feats.get("num_dataset_mentions", 0)
        + 0.3 * feats.get("num_metric_mentions", 0)
        + 0.5 * table_feats.get("pdf_num_comparison_tables", 0)
    )
    rigor = (
        empirical_depth
        + 0.8 * feats.get("has_error_bar_keyword", 0)
        + 0.4 * feats.get("has_hyperparameter_details", 0)
        + 0.4 * feats.get("has_code_url", 0)
    )
    theory_depth = (
        0.9 * feats.get("has_theorem", 0)
        + 0.6 * feats.get("has_lemma", 0)
        + 0.6 * feats.get("has_proof", 0)
        + 0.04 * min(50, feats.get("num_equation_markers", 0))
    )
    reproducibility = (
        0.8 * feats.get("has_code_url", 0)
        + 0.5 * feats.get("has_hyperparameter_details", 0)
        + 0.4 * feats.get("has_reproducibility_statement", 0)
        + 0.2 * feats.get("has_appendix", 0)
    )
    novelty = 1.1 * int("novel" in text.lower()) + 0.5 * int("first" in text.lower()) + 0.25 * min(8, pos)
    evidence_risk = 0.45 * neg + 0.8 * int(feats.get("has_baseline_keyword", 0) == 0 and feats.get("num_dataset_mentions", 0) > 0)
    length_ok = 1.0 if 3500 <= feats.get("num_tokens", 0) <= 25000 else 0.0
    reference_grounding = min(1.0, float(refs) / 35.0)
    table_strength = min(2.0, 0.25 * table_feats.get("pdf_num_comparison_tables", 0) + 0.08 * table_feats.get("pdf_table_metric_diversity", 0))
    raw = (
        -0.2
        + 0.30 * novelty
        + 0.22 * min(6, rigor)
        + 0.18 * min(4, theory_depth)
        + 0.20 * reproducibility
        + 0.25 * table_strength
        + 0.18 * reference_grounding
        + 0.10 * length_ok
        - 0.16 * min(8, evidence_risk)
    )
    p = 1.0 / (1.0 + math.exp(-raw))
    return {
        "judge_positive_term_count": float(pos),
        "judge_negative_term_count": float(neg),
        "judge_empirical_depth": float(empirical_depth),
        "judge_rigor_score": float(rigor),
        "judge_theory_depth": float(theory_depth),
        "judge_reproducibility_score": float(reproducibility),
        "judge_novelty_signal": float(novelty),
        "judge_evidence_risk": float(evidence_risk),
        "judge_reference_grounding": float(reference_grounding),
        "judge_table_strength": float(table_strength),
        "judge_accept_prior": float(clamp(p, 0.01, 0.99)),
        **table_feats,
    }

