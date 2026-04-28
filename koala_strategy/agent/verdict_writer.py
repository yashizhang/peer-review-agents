from __future__ import annotations

import re
from typing import Any

from koala_strategy.constants import COMMENT_REF_RE
from koala_strategy.discussion.citation_selector import select_citations
from koala_strategy.discussion.claim_extractor import extract_claim
from koala_strategy.models.score_mapping import probability_to_quality_percentile, percentile_to_koala_score, shrink_score_for_uncertainty
from koala_strategy.schemas import CommentRecord, PaperRecord, PredictionBundle, VerdictDraft
from koala_strategy.utils import clamp


def validate_verdict_body(
    body: str,
    score: float,
    citations: list[CommentRecord],
    agent_name: str,
    same_owner_agent_names: set[str] | None = None,
) -> None:
    same_owner_agent_names = same_owner_agent_names or set()
    if not (0.0 <= float(score) <= 10.0):
        raise ValueError("score must be in [0, 10]")
    refs = set(re.findall(COMMENT_REF_RE, body))
    expected = {c.comment_id for c in citations}
    if not expected <= refs:
        raise ValueError("verdict body is missing required comment refs")
    authors = {c.author_agent for c in citations}
    if agent_name in authors:
        raise ValueError("self citation is not allowed")
    if authors & same_owner_agent_names:
        raise ValueError("same-owner citation is not allowed")
    if len(authors) < 3:
        raise ValueError("at least 3 distinct external authors are required")


def prepare_verdict(
    paper: PaperRecord,
    prediction: PredictionBundle,
    comments: list[CommentRecord],
    agent_name: str,
    same_owner_agent_names: set[str] | None = None,
    discussion_update: dict[str, Any] | None = None,
    harness_context: dict[str, Any] | None = None,
) -> VerdictDraft:
    if paper.status != "deliberating":
        raise ValueError("paper must be deliberating")
    same_owner_agent_names = same_owner_agent_names or set()
    citations = select_citations(comments, agent_name, same_owner_agent_names, min_citations=3, max_citations=5)
    p_final = float((discussion_update or {}).get("p_accept_final", prediction.paper_only.get("p_accept", 0.5)))
    uncertainty = float(prediction.paper_only.get("uncertainty", 0.4))
    q = probability_to_quality_percentile(p_final)
    score = shrink_score_for_uncertainty(percentile_to_koala_score(q), uncertainty)
    if harness_context:
        h_score = harness_context.get("calibration", {}).get("harness_score_hint")
        if h_score is not None:
            score = 0.75 * score + 0.25 * float(h_score)
    score = round(clamp(score, 0.0, 10.0), 1)
    claims = [(c, extract_claim(c.comment_id, c.author_agent, c.content_markdown, c.owner_id)) for c in citations]
    positive_comments = [c for c, claim in claims if claim["polarity"] == "positive"] or [c for c, claim in claims if claim["polarity"] == "mixed"] or citations[:1]
    negative_comments = [c for c, claim in claims if claim["polarity"] == "negative"] or [c for c, claim in claims if claim["polarity"] == "mixed"] or citations[1:2]
    pos_use = positive_comments[:2]
    while len(pos_use) < 2 and len(pos_use) < len(citations):
        for candidate in citations:
            if candidate not in pos_use:
                pos_use.append(candidate)
                break
    neg_use = negative_comments[0] if negative_comments else next(c for c in citations if c not in pos_use)
    already = {c.comment_id for c in pos_use + [neg_use]}
    extra_comments = [c for c in citations if c.comment_id not in already]
    pos_refs = " and ".join(f"[[comment:{c.comment_id}]]" for c in pos_use)
    neg_ref = f"[[comment:{neg_use.comment_id}]]"
    extra = " ".join(f"[[comment:{c.comment_id}]]" for c in extra_comments)
    band = "weak accept" if score >= 6.0 else "borderline" if score >= 4.5 else "weak reject"
    positive_summary = "; ".join(prediction.paper_only.get("main_positive_evidence", [])[:2]) or "the paper has some concrete supporting evidence"
    negative_summary = "; ".join(prediction.paper_only.get("main_negative_evidence", [])[:2]) or "the paper still has decision-relevant risks"
    feedback = ""
    if harness_context:
        actions = [str(x) for x in harness_context.get("feedback_actions", []) if str(x).strip()]
        if actions:
            action = actions[0].rstrip(".")
            if action.startswith("Verify "):
                action = "verify " + action[len("Verify ") :]
                feedback = f" The strongest evidence-axis check for me is to {action}."
            else:
                feedback = f" The strongest evidence-axis check for me is: {action}."
    body = f"""**Verdict: {score:.1f}/10 — {band}**

I assign this score because the paper appears to be near the likely ICML acceptance threshold after weighing novelty, technical soundness, and evidence quality.

**Positive evidence.**
{positive_summary.rstrip(".")}. This is supported by {pos_refs}.

**Negative evidence / risks.**
{negative_summary.rstrip(".")}. The most decision-relevant concern is {neg_ref}, especially because it affects whether the strongest claim is fully established. {extra}

**Calibration.**
Relative to papers likely to clear an ICML accept threshold, I view this submission as {band}. The score is not higher because the remaining risks are central to the claim, and not lower because the paper still provides concrete evidence for a meaningful contribution.{feedback}

**Final score:** {score:.1f}
"""
    validate_verdict_body(body, score, citations, agent_name, same_owner_agent_names)
    return VerdictDraft(score=score, verdict_markdown=body, citation_ids=[c.comment_id for c in citations])
