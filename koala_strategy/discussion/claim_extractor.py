from __future__ import annotations

import re
from typing import Any


POSITIVE = ["strong", "positive", "support", "supports", "gain", "improve", "improves", "novel", "sound", "clear", "significant", "convincing", "outperform", "state-of-the-art"]
NEGATIVE = ["weak", "missing", "unclear", "limited", "flaw", "concern", "insufficient", "baseline", "ablation", "overclaim"]
FATAL = ["fatal", "invalid", "incorrect", "leak", "not sound", "cannot support", "major flaw"]
REPRO = ["reproduc", "code", "hyperparameter", "seed", "implementation"]
NOVELTY = ["novel", "prior work", "incremental", "related work", "novelty"]


def _contains_any(text: str, words: list[str]) -> bool:
    lower = text.lower()
    return any(word in lower for word in words)


def extract_claim(comment_id: str, author_agent: str, text: str, owner_id: str | None = None) -> dict[str, Any]:
    lower = (text or "").lower()
    claim_types: list[str] = []
    if _contains_any(lower, FATAL):
        claim_types.append("fatal_flaw")
    if _contains_any(lower, REPRO):
        claim_types.append("reproducibility_concern")
    if _contains_any(lower, NOVELTY):
        claim_types.append("novelty_prior_art")
    if "baseline" in lower or "ablation" in lower:
        claim_types.append("missing_baseline")
    if not claim_types:
        claim_types.append("general_quality")

    pos = sum(lower.count(w) for w in POSITIVE)
    neg = sum(lower.count(w) for w in NEGATIVE)
    if pos > neg * 1.4:
        polarity = "positive"
    elif neg > pos * 1.2:
        polarity = "negative"
    elif pos or neg:
        polarity = "mixed"
    else:
        polarity = "neutral"
    severity = 0
    if neg:
        severity = min(4, 1 + neg // 2)
    if _contains_any(lower, FATAL):
        severity = max(severity, 3)
    refs = re.findall(r"\b(?:Section|Sec\.|Table|Figure|Fig\.|Equation|Eq\.)\s+[A-Za-z0-9.:-]+", text or "", flags=re.I)
    specificity = min(1.0, 0.2 + 0.2 * len(refs) + 0.15 * int(len(text.split()) > 60) + 0.2 * int(any(ch.isdigit() for ch in text)))
    decision_relevance = min(1.0, 0.35 + 0.15 * len(set(claim_types)) + 0.15 * int(polarity in {"positive", "negative", "mixed"}))
    clarity = min(1.0, 0.3 + 0.3 * int(30 <= len(text.split()) <= 250) + 0.2 * int("." in text))
    quality_score = 0.35 * specificity + 0.25 * int(bool(refs)) + 0.20 * decision_relevance + 0.10 * clarity + 0.10
    return {
        "comment_id": comment_id,
        "author_agent": author_agent,
        "owner_id": owner_id,
        "claim_types": claim_types,
        "polarity": polarity,
        "severity": severity,
        "specificity": specificity,
        "decision_relevance": decision_relevance,
        "evidence_references": refs,
        "quality_score": min(1.0, quality_score),
        "summary": " ".join((text or "").split())[:240],
    }
