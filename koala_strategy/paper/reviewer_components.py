from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

from koala_strategy.paper.paper_features import extract_structured_features
from koala_strategy.paper.table_evidence import table_evidence_features
from koala_strategy.schemas import ParsedPaperText


COMPONENT_PATTERNS: dict[str, list[str]] = {
    "baseline_sota": [
        r"\bbaselines?\b",
        r"\bstate[- ]of[- ]the[- ]art\b",
        r"\bsota\b",
        r"\bcompar(?:e|ed|ison|isons)\b",
        r"\bprior methods?\b",
        r"\bexisting methods?\b",
    ],
    "ablation_sensitivity": [
        r"\bablation\b",
        r"\bsensitivity\b",
        r"\bdesign choices?\b",
        r"\bcomponent analysis\b",
        r"\bhyperparameters?\b",
        r"\bparameter sweep\b",
    ],
    "empirical_breadth": [
        r"\bdatasets?\b",
        r"\bbenchmarks?\b",
        r"\btasks?\b",
        r"\breal[- ]world\b",
        r"\bsynthetic\b",
        r"\btransfer\b",
        r"\bcross[- ]domain\b",
    ],
    "statistical_reliability": [
        r"\bseeds?\b",
        r"\bstandard deviations?\b",
        r"\bstd\.?\b",
        r"\bconfidence intervals?\b",
        r"\berror bars?\b",
        r"\bsignificance\b",
        r"\bp[- ]values?\b",
        r"\bvariance\b",
    ],
    "clarity_presentation": [
        r"\bclear(?:ly)?\b",
        r"\bunclear\b",
        r"\bnotation\b",
        r"\bexposition\b",
        r"\bpresentation\b",
        r"\bmotivation\b",
        r"\balgorithm\b",
        r"\bfigures?\b",
    ],
    "implementation_reproducibility": [
        r"\breproduc(?:e|ible|ibility)\b",
        r"\bimplementation\b",
        r"\bcode\b",
        r"\btraining details?\b",
        r"\bhyperparameters?\b",
        r"\bdata splits?\b",
        r"\bpreprocessing\b",
        r"\boptimizer\b",
        r"\blearning rate\b",
    ],
    "robustness_generalization": [
        r"\brobust(?:ness)?\b",
        r"\bgeneraliz(?:e|ation)\b",
        r"\bout[- ]of[- ]distribution\b",
        r"\bood\b",
        r"\bfailure cases?\b",
        r"\blimitations?\b",
        r"\bdomain shift\b",
        r"\bnoise\b",
    ],
    "efficiency_scalability_compute": [
        r"\bruntime\b",
        r"\blatency\b",
        r"\bmemory\b",
        r"\bcompute\b",
        r"\bcomputational cost\b",
        r"\bscalab(?:le|ility)\b",
        r"\boverhead\b",
        r"\binference time\b",
        r"\bgpu\b",
        r"\bflops?\b",
    ],
    "technical_soundness_claim_support": [
        r"\btheorems?\b",
        r"\blemmas?\b",
        r"\bproofs?\b",
        r"\bconvergence\b",
        r"\bassumptions?\b",
        r"\bderivations?\b",
        r"\bobjective\b",
        r"\bguarantees?\b",
        r"\bclaims?\b",
    ],
    "novelty_prior_work": [
        r"\bnovel(?:ty)?\b",
        r"\bcontributions?\b",
        r"\bincremental\b",
        r"\bprior work\b",
        r"\brelated work\b",
        r"\bfirst\b",
        r"\boriginal(?:ity)?\b",
    ],
}


CLAIM_STRENGTH_PATTERNS = [
    r"\bstate[- ]of[- ]the[- ]art\b",
    r"\bsota\b",
    r"\boutperform(?:s|ed)?\b",
    r"\bsignificant(?:ly)?\b",
    r"\bsubstantial(?:ly)?\b",
    r"\bnovel\b",
    r"\bfirst\b",
    r"\bguarantee(?:s|d)?\b",
]


@dataclass(frozen=True)
class ReviewerComponent:
    key: str
    label: str
    low_signal_template: str
    high_signal_template: str


REVIEWER_COMPONENTS = [
    ReviewerComponent(
        key="empirical_validation",
        label="Empirical validation and comparison completeness",
        low_signal_template="comparison evidence is thin: baselines, ablations, breadth, or statistical reliability are not strongly visible",
        high_signal_template="the paper shows concrete empirical support through comparisons, ablations, datasets, metrics, or tables",
    ),
    ReviewerComponent(
        key="clarity_reproducibility",
        label="Clarity, presentation, and reproducibility",
        low_signal_template="reproducibility hinges on whether implementation details, hyperparameters, code, or data splits are complete",
        high_signal_template="the paper includes signals of clear exposition or reproducibility detail",
    ),
    ReviewerComponent(
        key="practical_scope",
        label="Robustness, generalization, scalability, and compute",
        low_signal_template="scope beyond the reported setting is not fully resolved, especially robustness, failure modes, or compute cost",
        high_signal_template="the paper discusses robustness, generalization, failure modes, scalability, or cost",
    ),
    ReviewerComponent(
        key="technical_soundness",
        label="Technical soundness and claim support",
        low_signal_template="the strongest claims need to be checked against the actual assumptions, derivations, proofs, or evidence",
        high_signal_template="the paper contains technical support through assumptions, derivations, proofs, or direct claim-evidence links",
    ),
    ReviewerComponent(
        key="novelty_positioning",
        label="Novelty, contribution, and prior-work positioning",
        low_signal_template="novelty should be calibrated against the closest prior work and the paper's explicit contribution boundary",
        high_signal_template="the paper makes visible novelty or prior-work positioning claims",
    ),
]


def _count_patterns(patterns: list[str], text: str) -> int:
    return sum(len(re.findall(pattern, text, flags=re.I)) for pattern in patterns)


def _log_count(value: float) -> float:
    return float(math.log1p(max(0.0, value)))


def reviewer_component_features(parsed: ParsedPaperText) -> dict[str, float]:
    text = parsed.full_text or ""
    tokens = max(1, len(re.findall(r"\w+", text)))
    counts = {name: float(_count_patterns(patterns, text)) for name, patterns in COMPONENT_PATTERNS.items()}
    structured = extract_structured_features(parsed)
    table_feats = table_evidence_features(parsed.table_evidence)
    claim_strength = float(_count_patterns(CLAIM_STRENGTH_PATTERNS, text))

    baseline = counts["baseline_sota"] + structured.get("has_baseline_keyword", 0)
    ablation = counts["ablation_sensitivity"] + structured.get("has_ablation_keyword", 0)
    breadth = counts["empirical_breadth"] + structured.get("num_dataset_mentions", 0) + structured.get("num_metric_mentions", 0)
    stats = counts["statistical_reliability"] + structured.get("has_error_bar_keyword", 0)
    tables = table_feats.get("pdf_num_comparison_tables", 0.0) + 0.1 * table_feats.get("pdf_table_numeric_value_count", 0.0)

    empirical_signal = baseline + ablation + breadth + stats + tables
    clarity_repro_signal = counts["clarity_presentation"] + counts["implementation_reproducibility"]
    practical_signal = counts["robustness_generalization"] + counts["efficiency_scalability_compute"]
    technical_signal = counts["technical_soundness_claim_support"] + structured.get("has_theorem", 0) + structured.get("has_proof", 0)
    novelty_signal = counts["novelty_prior_work"] + min(10.0, structured.get("num_references", 0) / 8.0)

    features: dict[str, float] = {}
    for name, count in counts.items():
        features[f"review_component_{name}_count"] = count
        features[f"review_component_{name}_density"] = 1000.0 * count / tokens
        features[f"review_component_{name}_log_count"] = _log_count(count)

    features.update(
        {
            "review_component_claim_strength_count": claim_strength,
            "review_component_claim_strength_density": 1000.0 * claim_strength / tokens,
            "review_component_empirical_validation_signal": _log_count(empirical_signal),
            "review_component_clarity_reproducibility_signal": _log_count(clarity_repro_signal),
            "review_component_practical_scope_signal": _log_count(practical_signal),
            "review_component_technical_soundness_signal": _log_count(technical_signal),
            "review_component_novelty_positioning_signal": _log_count(novelty_signal),
            "review_component_baseline_ablation_balance": float(min(baseline, ablation) / max(1.0, max(baseline, ablation))),
            "review_component_empirical_stats_balance": float(min(empirical_signal, stats + 1.0) / max(1.0, empirical_signal)),
            "review_component_scope_efficiency_balance": float(
                min(counts["robustness_generalization"], counts["efficiency_scalability_compute"]) / max(1.0, practical_signal)
            ),
            "review_component_claim_evidence_gap": float(max(0.0, _log_count(claim_strength) - _log_count(empirical_signal + technical_signal))),
            "review_component_reproducibility_gap": float(max(0.0, _log_count(claim_strength) - _log_count(clarity_repro_signal))),
        }
    )
    return features


def component_signal_summary(parsed: ParsedPaperText) -> list[dict[str, Any]]:
    feats = reviewer_component_features(parsed)
    score_by_key = {
        "empirical_validation": feats["review_component_empirical_validation_signal"],
        "clarity_reproducibility": feats["review_component_clarity_reproducibility_signal"],
        "practical_scope": feats["review_component_practical_scope_signal"],
        "technical_soundness": feats["review_component_technical_soundness_signal"],
        "novelty_positioning": feats["review_component_novelty_positioning_signal"],
    }
    rows = []
    for component in REVIEWER_COMPONENTS:
        signal = float(score_by_key[component.key])
        rows.append(
            {
                "key": component.key,
                "label": component.label,
                "signal": signal,
                "assessment": component.high_signal_template if signal >= 1.3 else component.low_signal_template,
            }
        )
    return sorted(rows, key=lambda row: row["signal"])
