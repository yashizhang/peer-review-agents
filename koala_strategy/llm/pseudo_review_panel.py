from __future__ import annotations

import math
import statistics
from typing import Any

from koala_strategy.llm.prompts import PSEUDO_REVIEW_PERSONAS
from koala_strategy.paper.paper_features import extract_structured_features
from koala_strategy.schemas import ParsedPaperText, PseudoReview
from koala_strategy.utils import clamp, stable_hash


def _score(base: float, *adjustments: float) -> int:
    return int(round(clamp(base + sum(adjustments), 1, 10)))


def _band(mean_score: float, fatal: int) -> str:
    if fatal >= 3 or mean_score < 4.0:
        return "clear_reject"
    if mean_score < 5.2:
        return "weak_reject"
    if mean_score < 6.2:
        return "borderline"
    if mean_score < 7.4:
        return "weak_accept"
    return "strong_accept"


def heuristic_pseudo_review(parsed_paper: ParsedPaperText, persona: str) -> PseudoReview:
    feats = extract_structured_features(parsed_paper)
    text = parsed_paper.full_text.lower()
    jitter = ((stable_hash((parsed_paper.title or "") + persona) % 7) - 3) * 0.12
    rigor_bonus = 0.35 * feats["has_ablation_keyword"] + 0.25 * feats["has_baseline_keyword"] + 0.2 * feats["has_error_bar_keyword"]
    theory_bonus = 0.25 * feats["has_theorem"] + 0.2 * feats["has_proof"]
    code_bonus = 0.25 * feats["has_code_url"] + 0.15 * feats["has_hyperparameter_details"]
    novelty_bonus = 0.45 * int("novel" in text or "new" in text) + 0.2 * int("state-of-the-art" in text or "sota" in text)
    risk = 0.0
    risk += 0.45 * int("limited" in text or "limitation" in text)
    risk += 0.3 * int(feats["has_baseline_keyword"] == 0 and feats["num_dataset_mentions"] > 0)
    risk += 0.25 * int(feats["has_ablation_keyword"] == 0 and feats["num_dataset_mentions"] > 0)
    risk += 0.2 * int(feats["num_tokens"] < 180)

    persona_bias = {
        "technical_skeptic": (-0.2, -0.4, 0.0),
        "experimental_rigor_auditor": (0.0, -0.15, -0.35),
        "novelty_and_prior_work_checker": (-0.35, 0.0, 0.0),
        "clarity_and_significance_reviewer": (0.0, 0.15, 0.0),
        "area_chair_calibrator": (0.0, 0.0, 0.0),
    }.get(persona, (0.0, 0.0, 0.0))

    novelty = _score(5.4, novelty_bonus, persona_bias[0], jitter)
    soundness = _score(5.4, theory_bonus, -0.5 * risk, persona_bias[1], jitter)
    empirical = _score(5.2, rigor_bonus, -0.65 * risk, persona_bias[2], jitter)
    clarity = _score(5.8, 0.35 * int(feats["num_sections"] >= 4), -0.25 * int(feats["num_tokens"] < 250), jitter)
    significance = _score(5.3, novelty_bonus, 0.15 * feats["has_sota_keyword"], -0.3 * risk, jitter)
    reproducibility = _score(4.9, code_bonus, 0.2 * feats["has_hyperparameter_details"], -0.25 * risk, jitter)
    alignment = _score(5.3, rigor_bonus, theory_bonus, -0.4 * risk, jitter)
    missing_baseline = int(clamp(2 + (1 - feats["has_baseline_keyword"]) + (1 - feats["has_ablation_keyword"]) - feats["has_theorem"], 0, 4))
    fatal = int(clamp(math.floor(risk + int(soundness <= 3) + int(empirical <= 3)), 0, 4))
    mean_score = statistics.mean([novelty, soundness, empirical, clarity, significance, reproducibility, alignment])
    return PseudoReview(
        persona=persona,
        novelty=novelty,
        technical_soundness=soundness,
        empirical_rigor=empirical,
        clarity=clarity,
        significance=significance,
        reproducibility=reproducibility,
        claim_evidence_alignment=alignment,
        missing_baseline_severity=missing_baseline,
        fatal_flaw_severity=fatal,
        fatal_flaws=["Evidence gap may affect the main claim."] if fatal >= 3 else [],
        strongest_accept_signal="The paper states a concrete contribution and reports decision-relevant evidence.",
        strongest_reject_signal="The main risk is whether the evidence fully supports the strongest claim.",
        confidence=int(clamp(2 + feats["has_abstract"] + int(feats["num_dataset_mentions"] > 0) + int(feats["has_theorem"]), 1, 5)),
        recommended_score_band=_band(mean_score, fatal),
        short_rationale="Heuristic pseudo-review based only on paper text, structure, and claim/evidence signals.",
    )


def run_pseudo_review_panel(parsed_paper: ParsedPaperText) -> list[PseudoReview]:
    return [heuristic_pseudo_review(parsed_paper, persona) for persona in PSEUDO_REVIEW_PERSONAS]


def _stats(values: list[float], prefix: str) -> dict[str, float]:
    if not values:
        return {f"mean_{prefix}": 0.0, f"min_{prefix}": 0.0, f"max_{prefix}": 0.0, f"std_{prefix}": 0.0}
    return {
        f"mean_{prefix}": float(statistics.mean(values)),
        f"min_{prefix}": float(min(values)),
        f"max_{prefix}": float(max(values)),
        f"std_{prefix}": float(statistics.pstdev(values)) if len(values) > 1 else 0.0,
    }


def aggregate_pseudo_reviews(reviews: list[PseudoReview]) -> dict[str, float]:
    if not reviews:
        return {}
    data: dict[str, float] = {}
    for field in ["novelty", "technical_soundness", "empirical_rigor"]:
        data.update(_stats([float(getattr(r, field)) for r in reviews], field))
    for field in ["clarity", "significance", "reproducibility", "claim_evidence_alignment", "confidence"]:
        data[f"mean_{field}"] = float(statistics.mean(float(getattr(r, field)) for r in reviews))
    data["max_fatal_flaw_severity"] = float(max(r.fatal_flaw_severity for r in reviews))
    data["mean_fatal_flaw_severity"] = float(statistics.mean(r.fatal_flaw_severity for r in reviews))
    data["mean_missing_baseline_severity"] = float(statistics.mean(r.missing_baseline_severity for r in reviews))
    means = [r.novelty + r.technical_soundness + r.empirical_rigor + r.significance for r in reviews]
    data["panel_disagreement"] = float(statistics.pstdev(means) / 10.0) if len(means) > 1 else 0.0
    data["num_acceptish_reviewers"] = float(sum(r.recommended_score_band in {"weak_accept", "strong_accept"} for r in reviews))
    data["num_rejectish_reviewers"] = float(sum(r.recommended_score_band in {"weak_reject", "clear_reject"} for r in reviews))
    return data


def panel_features_for_text(title: str, abstract: str | None, full_text: str | None = None) -> dict[str, float]:
    from koala_strategy.paper.pdf_parser import parsed_from_record

    return aggregate_pseudo_reviews(run_pseudo_review_panel(parsed_from_record(title, abstract, full_text)))

