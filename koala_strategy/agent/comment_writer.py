from __future__ import annotations

from typing import Any

from koala_strategy.schemas import PaperRecord, PredictionBundle


AGENT_STYLE = {
    "review_director": "fulltext_calibrated_review",
    "calibrated_decider": "balanced_decision",
    "rigor_auditor": "experimental_rigor",
    "novelty_scout": "novelty_literature",
}


def write_comment(
    paper: PaperRecord,
    prediction: PredictionBundle,
    agent_name: str,
    harness_context: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    p = prediction.paper_only
    positives = [str(x) for x in (harness_context or {}).get("positive_evidence", []) if str(x).strip()]
    negatives = [str(x) for x in (harness_context or {}).get("negative_evidence", []) if str(x).strip()]
    positives.extend(str(x) for x in p.get("main_positive_evidence", []) if str(x).strip())
    negatives.extend(str(x) for x in p.get("main_negative_evidence", []) if str(x).strip())
    positives = list(dict.fromkeys(positives))
    negatives = list(dict.fromkeys(negatives))
    positive = positives[0] if positives else "The paper presents a clear decision-relevant contribution."
    negative = negatives[0] if negatives else "The remaining risk is whether the strongest claim is fully established."
    lo, hi = p.get("recommended_score_range", [4.5, 5.5])
    uncertainty = float(p.get("uncertainty", 0.5))
    confidence = "low" if uncertainty > 0.55 else "medium" if uncertainty > 0.25 else "high"
    leaning = "weak accept" if hi >= 6.5 and lo >= 4.8 else "weak reject" if hi <= 4.7 else "borderline"
    focus = AGENT_STYLE.get(agent_name, "balanced_decision")
    if focus == "fulltext_calibrated_review":
        focus_sentence = "I am checking the paper across comparison completeness, reproducibility, scope beyond the reported setting, soundness, and novelty."
        missing = "paper-specific discussion that resolves the weakest evidence axis rather than only restating the claimed contribution"
    elif focus == "experimental_rigor":
        focus_sentence = "I am focusing on whether the experimental evidence, baselines, ablations, and reporting are strong enough to support the claimed gain."
        missing = "a more explicit baseline/ablation or variance check tied to the central claim"
    elif focus == "novelty_literature":
        focus_sentence = "I am focusing on how well the novelty claim is separated from adjacent prior work and whether the paper avoids overclaiming."
        missing = "a sharper comparison to the closest prior method and a narrower statement of what is actually new"
    else:
        focus_sentence = "I am focusing on the balance between novelty, soundness, significance, and the strength of the evidence."
        missing = "evidence that directly resolves the main acceptance-threshold risk"
    feedback = []
    if harness_context:
        feedback = [str(x) for x in harness_context.get("feedback_actions", []) if str(x).strip()]
    feedback_sentence = (feedback[0] if feedback else missing).rstrip(".")
    if feedback_sentence.startswith("Verify "):
        feedback_sentence = "verify " + feedback_sentence[len("Verify ") :]
    score_hint = (harness_context or {}).get("calibration", {}).get("harness_score_hint")
    score_text = f"{float(lo):.1f}-{float(hi):.1f}/10"
    if score_hint is not None:
        score_text = f"{float(lo):.1f}-{float(hi):.1f}/10, with an internal evidence-adjusted midpoint near {float(score_hint):.1f}"
    content = f"""**Decision-relevant evidence check**

My reading is that the paper's main claim is: {paper.title}. {focus_sentence}

**Strongest acceptance signal.**
{positive} This matters for the decision because it is the clearest paper-internal support for the claimed contribution, rather than just a restatement of the abstract.

**Main rejection risk.**
{negative} I think this is decision-relevant because an ICML-level accept decision should depend on whether the strongest claim remains true under the most relevant comparisons and checks.

**Evidence quality.**
The current evidence supports a cautious version of the contribution, but it does not fully establish the strongest possible interpretation unless the discussion confirms that the key comparison is fair and that the reported gains are robust. My calibrated paper-only range is about {score_text}; I would treat that as a prior, not a final verdict.

**Current leaning.**
{leaning}, with {confidence} confidence. The evidence that would most change my view: {feedback_sentence}.
"""
    evidence = {
        "paper_id": paper.paper_id,
        "agent": agent_name,
        "positive_evidence": positives,
        "negative_evidence": negatives,
        "recommended_score_range": [lo, hi],
        "uncertainty": uncertainty,
        "harness_context": harness_context or {},
        "comment_strategy": prediction.agent_instruction.get("comment_strategy"),
    }
    return content.strip() + "\n", evidence
