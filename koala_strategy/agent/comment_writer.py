from __future__ import annotations

import re
from typing import Any

from koala_strategy.schemas import PaperRecord, PredictionBundle


AGENT_STYLE = {
    "review_director": "fulltext_calibrated_review",
    "calibrated_decider": "balanced_decision",
    "rigor_auditor": "experimental_rigor",
    "novelty_scout": "novelty_literature",
}


def _first_claim(paper: PaperRecord) -> str:
    text = " ".join((paper.abstract or paper.full_text or paper.title or "").split())
    if not text:
        return paper.title
    sentences = re.split(r"(?<=[.!?])\s+", text)
    for sent in sentences:
        clean = sent.strip()
        if 60 <= len(clean) <= 280:
            return clean
    return text[:260].rstrip() + ("..." if len(text) > 260 else "")


def _clip(text: str, max_len: int = 320) -> str:
    clean = " ".join(str(text or "").split())
    if len(clean) > max_len:
        clean = clean[: max_len - 3].rstrip() + "..."
    return clean


def _dedupe(items: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        clean = _clip(item)
        key = clean.lower()
        if clean and key not in seen:
            out.append(clean)
            seen.add(key)
    return out


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
    positives = _dedupe(positives)
    negatives = _dedupe(negatives)
    positive = positives[0] if positives else "The paper states a concrete contribution and reports supporting evidence."
    negative = negatives[0] if negatives else "The main unresolved risk is whether the strongest claim is fully supported under fair comparisons and robust checks."
    uncertainty = float(p.get("uncertainty", 0.5))
    confidence = "low" if uncertainty > 0.55 else "medium" if uncertainty > 0.25 else "high"
    focus = AGENT_STYLE.get(agent_name, "balanced_decision")
    if focus == "fulltext_calibrated_review":
        focus_sentence = "I checked comparison completeness, reproducibility, scope beyond the reported setting, soundness, and novelty."
        missing = "verify the weakest evidence axis with a paper-specific comparison rather than restating the contribution"
    elif focus == "experimental_rigor":
        focus_sentence = "I focused on whether baselines, ablations, variance reporting, and evaluation protocol support the claimed gain."
        missing = "add or verify a baseline/ablation or variance check tied to the central claim"
    elif focus == "novelty_literature":
        focus_sentence = "I focused on whether the novelty claim is clearly separated from adjacent prior work and whether the paper overclaims."
        missing = "sharpen the comparison to the closest prior method and narrow the novelty claim"
    else:
        focus_sentence = "I focused on the balance between novelty, soundness, significance, and evidence strength."
        missing = "resolve the main acceptance-threshold risk with direct evidence"
    feedback = [str(x) for x in (harness_context or {}).get("feedback_actions", []) if str(x).strip()]
    feedback_sentence = _clip((feedback[0] if feedback else missing).rstrip("."), 260)
    if feedback_sentence.startswith("Verify "):
        feedback_sentence = "verify " + feedback_sentence[len("Verify ") :]

    claim = _first_claim(paper)
    lean = "upward" if float(p.get("p_accept", 0.5)) >= 0.60 else "downward" if float(p.get("p_accept", 0.5)) <= 0.40 else "borderline"
    lean_sentence = {
        "upward": "I lean positive only if the central comparison and protocol hold up under discussion.",
        "downward": "I lean negative unless another reviewer verifies that the central risk is already addressed in the paper.",
        "borderline": "I view the paper as borderline until the discussion resolves the key evidence gap.",
    }[lean]

    content = f"""**Decision-relevant evidence check**

**Claim I am evaluating.** {claim}

**What I checked.** {focus_sentence}

**Strongest paper-internal support.**
{positive} This is decision-relevant because it is the clearest support I found for the contribution rather than a generic restatement of the abstract.

**Main risk.**
{negative} This matters because the decision should turn on whether the strongest claim survives fair comparisons, ablations, and reasonable scope limits.

**How I would update.**
{lean_sentence} The specific check that would most change my view is to {feedback_sentence}.

**Confidence.** {confidence}. I am treating this as a review note for discussion, not as a final verdict.
"""
    evidence = {
        "paper_id": paper.paper_id,
        "agent": agent_name,
        "claim_checked": claim,
        "positive_evidence": positives,
        "negative_evidence": negatives,
        "recommended_score_range": p.get("recommended_score_range"),
        "uncertainty": uncertainty,
        "harness_context": harness_context or {},
        "comment_strategy": prediction.agent_instruction.get("comment_strategy"),
    }
    return content.strip() + "\n", evidence
