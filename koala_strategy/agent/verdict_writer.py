from __future__ import annotations

import re
from typing import Any

from koala_strategy.constants import COMMENT_REF_RE
from koala_strategy.schemas import CommentRecord, PaperRecord, PredictionBundle, VerdictDraft
from koala_strategy.discussion.citation_selector import select_citations
from koala_strategy.discussion.claim_extractor import extract_claim
from koala_strategy.models.score_mapping import probability_to_quality_percentile, percentile_to_koala_score, shrink_score_for_uncertainty
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


def _one_line(text: str, max_len: int = 220) -> str:
    clean = " ".join(str(text or "").split())
    if len(clean) > max_len:
        clean = clean[: max_len - 3].rstrip() + "..."
    return clean or "a decision-relevant point from the discussion"


def _score_band(score: float) -> str:
    if score < 3.0:
        return "clear reject"
    if score < 5.0:
        return "weak reject"
    if score < 7.0:
        return "weak accept"
    if score < 9.0:
        return "strong accept"
    return "spotlight-quality accept"


def prepare_verdict(
    paper: PaperRecord,
    prediction: PredictionBundle,
    comments: list[CommentRecord],
    agent_name: str,
    same_owner_agent_names: set[str] | None = None,
    discussion_update: dict[str, Any] | None = None,
    harness_context: dict[str, Any] | None = None,
    min_comment_quality: float = 0.0,
) -> VerdictDraft:
    if paper.status != "deliberating":
        raise ValueError("paper must be deliberating")
    same_owner_agent_names = same_owner_agent_names or set()
    citations = select_citations(
        comments,
        agent_name,
        same_owner_agent_names,
        min_citations=3,
        max_citations=5,
        min_quality=min_comment_quality,
    )
    p_final = float((discussion_update or {}).get("p_accept_final", prediction.paper_only.get("p_accept", 0.5)))
    uncertainty = float(prediction.paper_only.get("uncertainty", 0.4))
    q = probability_to_quality_percentile(p_final)
    score = shrink_score_for_uncertainty(percentile_to_koala_score(q), uncertainty)
    if harness_context:
        h_score = harness_context.get("calibration", {}).get("harness_score_hint")
        if h_score is not None:
            score = 0.75 * score + 0.25 * float(h_score)
    score = round(clamp(score, 0.0, 10.0), 1)
    band = _score_band(score)

    claims = [(c, extract_claim(c.comment_id, c.author_agent, c.content_markdown, c.owner_id)) for c in citations]
    positive_comments = [c for c, claim in claims if claim["polarity"] == "positive"]
    negative_comments = [c for c, claim in claims if claim["polarity"] == "negative"]
    mixed_comments = [c for c, claim in claims if claim["polarity"] == "mixed"]
    if not positive_comments:
        positive_comments = mixed_comments[:1] or citations[:1]
    if not negative_comments:
        negative_comments = mixed_comments[1:2] or [c for c in citations if c not in positive_comments][:1] or citations[:1]
    pos_use = positive_comments[:2]
    while len(pos_use) < 2 and len(pos_use) < len(citations):
        for candidate in citations:
            if candidate not in pos_use:
                pos_use.append(candidate)
                break
    neg_use = negative_comments[0]
    already = {c.comment_id for c in pos_use + [neg_use]}
    extra_comments = [c for c in citations if c.comment_id not in already]

    pos_refs = " and ".join(f"[[comment:{c.comment_id}]]" for c in pos_use)
    neg_ref = f"[[comment:{neg_use.comment_id}]]"
    extra = " ".join(f"[[comment:{c.comment_id}]]" for c in extra_comments)
    positive_summary = _one_line("; ".join(prediction.paper_only.get("main_positive_evidence", [])[:2]) or "The paper has concrete supporting evidence.")
    negative_summary = _one_line("; ".join(prediction.paper_only.get("main_negative_evidence", [])[:2]) or "The paper has remaining risks around the central claim.")
    pos_discussion = _one_line(pos_use[0].content_markdown)
    neg_discussion = _one_line(neg_use.content_markdown)
    feedback = ""
    if harness_context:
        actions = [str(x) for x in harness_context.get("feedback_actions", []) if str(x).strip()]
        if actions:
            action = actions[0].rstrip(".")
            if action.startswith("Verify "):
                action = "verify " + action[len("Verify ") :]
            feedback = f" The most useful remaining check is to {action}."

    body = f"""**Verdict: {score:.1f}/10 — {band}**

I score this paper as **{band}** after weighing paper-internal evidence against the current discussion.

**Why the paper could clear the bar.**
{positive_summary}. The strongest external support I found is {pos_refs}; in particular, one cited discussion point says: "{pos_discussion}".

**Why I am not scoring it higher.**
{negative_summary}. The most decision-relevant concern is {neg_ref}; that concern is: "{neg_discussion}". {extra}

**Calibration.**
My score is close to the acceptance threshold when the supporting evidence is concrete but the central risk remains material. I would move upward if the discussion establishes that the main comparison and protocol are fair; I would move downward if the key gain relies on an omitted baseline, narrow setting, or overbroad claim.{feedback}

**Final score:** {score:.1f}
"""
    validate_verdict_body(body, score, citations, agent_name, same_owner_agent_names)
    return VerdictDraft(score=score, verdict_markdown=body, citation_ids=[c.comment_id for c in citations])
