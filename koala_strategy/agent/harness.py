from __future__ import annotations

import statistics
from typing import Any

from koala_strategy.models.fulltext_evidence_model import parsed_payload_for_paper
from koala_strategy.llm.review_judge import llm_probability_for_blend, run_llm_review_judge
from koala_strategy.paper.paper_features import evidence_snippets, parsed_from_paper_record
from koala_strategy.paper.reviewer_components import component_signal_summary, reviewer_component_features
from koala_strategy.paper.table_evidence import best_table_evidence
from koala_strategy.schemas import PaperRecord, PredictionBundle, ParsedPaperText
from koala_strategy.utils import clamp


ROLE_ORDER = [
    "evidence_mapper",
    "empirical_auditor",
    "reproducibility_auditor",
    "scope_critic",
    "soundness_checker",
    "novelty_scout",
    "calibration_chair",
]


def _parsed_for_harness(paper: PaperRecord) -> ParsedPaperText:
    payload = parsed_payload_for_paper(paper.paper_id)
    if payload:
        return ParsedPaperText.model_validate(payload)
    return parsed_from_paper_record(paper)


def _best_evidence(parsed: ParsedPaperText, prediction: PredictionBundle) -> tuple[list[str], list[str]]:
    positives = [str(x) for x in prediction.paper_only.get("main_positive_evidence", []) if str(x).strip()]
    negatives = [str(x) for x in prediction.paper_only.get("main_negative_evidence", []) if str(x).strip()]
    if parsed.table_evidence:
        for item in best_table_evidence(parsed.table_evidence, limit=3):
            context = " ".join(str(item.get("caption_or_context", "")).split())[:360]
            positives.append(
                f"Table {item.get('table_id')} reports comparison-style evidence with "
                f"{item.get('num_numeric_values', 0)} numeric values"
                + (f" and metrics {', '.join(item.get('metrics') or [])}" if item.get("metrics") else "")
                + f": {context}"
            )
    snippets = evidence_snippets(parsed)
    positives.extend(str(x) for x in snippets.get("positive", []) if str(x).strip())
    negatives.extend(str(x) for x in snippets.get("negative", []) if str(x).strip())
    return _dedupe(positives)[:5], _dedupe(negatives)[:5]


def _dedupe(items: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        key = " ".join(item.lower().split())[:180]
        if key and key not in seen:
            seen.add(key)
            out.append(item)
    return out


def _stance_from_score(score: float) -> str:
    if score >= 6.5:
        return "accept-leaning"
    if score <= 4.2:
        return "reject-leaning"
    return "borderline"


def _role_output(role: str, component: dict[str, Any] | None, base_score: float, positives: list[str], negatives: list[str]) -> dict[str, Any]:
    signal = float(component.get("signal", 0.0) if component else 0.0)
    label = str(component.get("label", role) if component else role)
    assessment = str(component.get("assessment", "") if component else "")
    role_bias = {
        "empirical_auditor": 0.18,
        "reproducibility_auditor": 0.10,
        "scope_critic": 0.08,
        "soundness_checker": 0.16,
        "novelty_scout": 0.14,
    }.get(role, 0.0)
    role_score = clamp(base_score + role_bias * (signal - 1.25), 0.0, 10.0)
    concern = assessment
    if signal >= 1.3:
        evidence = positives[:2]
    else:
        evidence = negatives[:2] or positives[:1]
    return {
        "role": role,
        "component": label,
        "signal": round(signal, 3),
        "stance": _stance_from_score(role_score),
        "score_hint": round(role_score, 2),
        "confidence": round(clamp(0.35 + 0.12 * signal + 0.08 * len(evidence), 0.1, 0.9), 2),
        "evidence": evidence,
        "concern": concern,
    }


def _llm_enabled(config: dict[str, Any] | None) -> bool:
    if not config:
        return False
    models = config.get("models", {})
    return bool(models.get("use_llm_harness", False)) and str(models.get("llm_provider", "heuristic")).lower() != "heuristic"


def run_internal_review_harness(
    paper: PaperRecord,
    prediction: PredictionBundle,
    agent_name: str = "review_director",
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    parsed = _parsed_for_harness(paper)
    positives, negatives = _best_evidence(parsed, prediction)
    component_rows = component_signal_summary(parsed)
    component_by_key = {row["key"]: row for row in component_rows}
    feats = reviewer_component_features(parsed)
    lo, hi = prediction.paper_only.get("recommended_score_range", [4.5, 5.5])
    base_score = (float(lo) + float(hi)) / 2.0

    role_outputs = [
        {
            "role": "evidence_mapper",
            "component": "Paper-internal evidence extraction",
            "signal": round(float(feats.get("review_component_empirical_validation_signal", 0.0)), 3),
            "stance": _stance_from_score(base_score),
            "score_hint": round(base_score, 2),
            "confidence": round(clamp(0.35 + 0.05 * len(positives) + 0.03 * len(parsed.table_evidence), 0.1, 0.9), 2),
            "evidence": positives[:3],
            "concern": negatives[0] if negatives else "The main risk is whether the paper's strongest claim is fully supported.",
        },
        _role_output("empirical_auditor", component_by_key.get("empirical_validation"), base_score, positives, negatives),
        _role_output("reproducibility_auditor", component_by_key.get("clarity_reproducibility"), base_score, positives, negatives),
        _role_output("scope_critic", component_by_key.get("practical_scope"), base_score, positives, negatives),
        _role_output("soundness_checker", component_by_key.get("technical_soundness"), base_score, positives, negatives),
        _role_output("novelty_scout", component_by_key.get("novelty_positioning"), base_score, positives, negatives),
    ]

    score_hints = [float(r["score_hint"]) for r in role_outputs]
    concern_penalty = 0.18 * float(feats.get("review_component_claim_evidence_gap", 0.0))
    concern_penalty += 0.08 * float(feats.get("review_component_reproducibility_gap", 0.0))
    adjusted_score = clamp(statistics.mean(score_hints) - concern_penalty, 0.0, 10.0)
    calibration = {
        "role": "calibration_chair",
        "component": "Calibrated verdict prior",
        "signal": round(1.0 - float(prediction.paper_only.get("uncertainty", 0.5)), 3),
        "stance": _stance_from_score(adjusted_score),
        "score_hint": round(adjusted_score, 2),
        "confidence": round(clamp(1.0 - float(prediction.paper_only.get("uncertainty", 0.5)), 0.1, 0.9), 2),
        "evidence": positives[:2],
        "concern": "Use discussion evidence to move this prior only when cited external comments add paper-specific evidence.",
    }
    role_outputs.append(calibration)

    llm_judge: dict[str, Any] | None = None
    if _llm_enabled(config):
        llm_judge = run_llm_review_judge(paper, prediction, config=config)
        llm_score = float(llm_judge.get("verdict_score", adjusted_score))
        llm_conf = float(llm_judge.get("confidence", 0.5))
        llm_p = llm_probability_for_blend(llm_judge)
        role_outputs.append(
            {
                "role": "llm_brain",
                "component": "LLM structured peer-review judgment",
                "signal": round(llm_conf, 3),
                "stance": _stance_from_score(llm_score),
                "score_hint": round(llm_score, 2),
                "confidence": round(llm_conf, 2),
                "evidence": [llm_judge.get("strongest_accept_signal", "")],
                "concern": llm_judge.get("strongest_reject_signal", ""),
                "axes": llm_judge.get("axes", {}),
            }
        )
        blend_weight = 0.25 + 0.25 * clamp(llm_conf, 0.0, 1.0)
        adjusted_score = clamp((1.0 - blend_weight) * adjusted_score + blend_weight * llm_score, 0.0, 10.0)
        positives = _dedupe([str(llm_judge.get("strongest_accept_signal", "")), *positives])
        negatives = _dedupe([str(llm_judge.get("strongest_reject_signal", "")), *negatives])

    low_components = [row for row in component_rows if float(row["signal"]) < 1.3]
    feedback_actions = [
        row["assessment"] for row in low_components[:3]
    ] or ["Verify that the top evidence is not just abstract-level support before submitting a verdict."]
    if llm_judge:
        feedback_actions = [*llm_judge.get("feedback_actions", []), *feedback_actions]
    return {
        "agent": agent_name,
        "paper_id": paper.paper_id,
        "roles": role_outputs,
        "component_summary": component_rows,
        "positive_evidence": positives[:5],
        "negative_evidence": negatives[:5],
        "feedback_actions": feedback_actions,
        "llm_judge": llm_judge,
        "calibration": {
            "base_score_midpoint": round(base_score, 2),
            "harness_score_hint": round(adjusted_score, 2),
            "p_accept": float(prediction.paper_only.get("p_accept", 0.5)),
            "llm_p_accept": llm_probability_for_blend(llm_judge) if llm_judge else None,
            "uncertainty": float(prediction.paper_only.get("uncertainty", 0.5)),
            "stance": _stance_from_score(adjusted_score),
        },
    }
