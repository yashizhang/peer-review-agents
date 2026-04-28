from __future__ import annotations

import re
from collections import Counter
from typing import Any

from koala_strategy.schemas import ParsedPaperText, PaperRecord


DATASET_PATTERNS = re.compile(
    r"\b(ImageNet|CIFAR|MNIST|COCO|WMT|GLUE|SuperGLUE|MATH|GSM8K|AIME|HumanEval|MMLU|LibriSpeech|KITTI|MuJoCo|Atari|D4RL|HotpotQA|NaturalQuestions|PubMed|MIMIC|LIBERO|CALVIN)\b",
    re.I,
)
METRIC_PATTERNS = re.compile(r"\b(accuracy|F1|AUROC|AUC|BLEU|ROUGE|perplexity|success rate|regret|RMSE|MAE|mAP|IoU|WER)\b", re.I)


def _has(pattern: str, text: str) -> int:
    return int(re.search(pattern, text, flags=re.I) is not None)


def _count(pattern: str, text: str) -> int:
    return len(re.findall(pattern, text, flags=re.I))


def extract_structured_features(parsed: ParsedPaperText) -> dict[str, float | int | str]:
    text = parsed.full_text or ""
    lower = text.lower()
    tokens = re.findall(r"\w+", text)
    refs = parsed.references
    years = [int(y) for y in re.findall(r"\b(20[0-2]\d|19[8-9]\d)\b", "\n".join(refs))]
    recent = [y for y in years if y >= 2022]
    features: dict[str, float | int | str] = {
        "num_pages_estimate": max(1, _count(r"\[Page\s+\d+\]", text) or int(len(tokens) / 750) + 1),
        "num_tokens": len(tokens),
        "num_sections": len(parsed.sections),
        "has_abstract": int(bool(parsed.abstract)),
        "has_limitations_section": _has(r"\blimitations?\b", lower),
        "has_reproducibility_statement": _has(r"\breproducibility|reproducible|artifact|code availability\b", lower),
        "num_tables": max(len(parsed.table_captions), _count(r"\btable\s+\d+", text)),
        "num_figures": max(len(parsed.figure_captions), _count(r"\bfigure\s+\d+|\bfig\.\s*\d+", text)),
        "has_ablation_keyword": _has(r"\bablation", lower),
        "has_baseline_keyword": _has(r"\bbaselines?\b|compared with|compare to", lower),
        "has_sota_keyword": _has(r"state-of-the-art|\bsota\b", lower),
        "has_error_bar_keyword": _has(r"error bars?|standard deviation|std\.?|confidence interval", lower),
        "has_confidence_interval_keyword": _has(r"confidence interval|\b95%\s*ci\b", lower),
        "has_statistical_significance_keyword": _has(r"statistical significance|p-value|significance test", lower),
        "num_dataset_mentions": len(DATASET_PATTERNS.findall(text)),
        "num_metric_mentions": len(METRIC_PATTERNS.findall(text)),
        "has_theorem": _has(r"\btheorem\b", lower),
        "has_lemma": _has(r"\blemma\b", lower),
        "has_proposition": _has(r"\bproposition\b", lower),
        "has_proof": _has(r"\bproof\b", lower),
        "num_equation_markers": _count(r"\(\d+\)|\\begin\{equation\}|\$[^$]{3,}\$", text),
        "assumption_keyword_count": _count(r"\bassumptions?\b", lower),
        "has_github_url": _has(r"github\.com", lower),
        "has_code_url": _has(r"github\.com|gitlab\.com|bitbucket\.org|code available|anonymous\.4open", lower),
        "has_appendix": _has(r"\bappendix\b|supplementary", lower),
        "has_hyperparameter_details": _has(r"hyperparameter|learning rate|batch size|optimizer", lower),
        "num_references": len(refs),
        "recent_reference_ratio_estimate": len(recent) / max(1, len(years)),
        "arxiv_reference_count": _count(r"arxiv", "\n".join(refs).lower()),
    }
    return features


def parsed_from_paper_record(paper: PaperRecord) -> ParsedPaperText:
    from koala_strategy.paper.pdf_parser import parsed_from_record

    return parsed_from_record(paper.title, paper.abstract, paper.full_text)


def feature_text_for_record(record: Any) -> str:
    if hasattr(record, "title"):
        title = getattr(record, "title", "")
        abstract = getattr(record, "abstract", "")
        domains = " ".join(getattr(record, "domains", []) or [])
        meta = getattr(record, "metadata", {}) or {}
    else:
        title = record.get("title", "")
        abstract = record.get("abstract", "")
        domains = " ".join(record.get("domains", []) or [])
        meta = record
    bits = [
        title,
        abstract or "",
        meta.get("tldr", "") if isinstance(meta, dict) else "",
        " ".join(meta.get("keywords", []) or []) if isinstance(meta, dict) else "",
        meta.get("primary_area", "") if isinstance(meta, dict) else "",
        domains,
    ]
    return "\n".join(str(x) for x in bits if x)


def evidence_snippets(parsed: ParsedPaperText) -> dict[str, list[str]]:
    text = parsed.full_text
    sentences = re.split(r"(?<=[.!?])\s+", text)
    buckets = {
        "positive": ["state-of-the-art", "outperform", "improve", "novel", "theorem", "guarantee"],
        "negative": ["limitation", "however", "fail", "missing", "only", "assume", "sensitive"],
        "rigor": ["ablation", "baseline", "standard deviation", "confidence interval", "hyperparameter"],
    }
    out: dict[str, list[str]] = {}
    for name, keys in buckets.items():
        scored = []
        for sent in sentences:
            s = " ".join(sent.split())
            if 40 <= len(s) <= 320:
                lower_s = s.lower()
                if name == "negative" and any(pos in lower_s for pos in ["outperform", "improve", "improved", "state-of-the-art", "achieves improved"]):
                    if not any(neg in lower_s for neg in ["however", "limitation", "missing", "fail", "sensitive"]):
                        continue
                score = sum(k in s.lower() for k in keys)
                if score:
                    scored.append((score, s))
        scored.sort(reverse=True)
        out[name] = [s for _, s in scored[:3]]
    return out
