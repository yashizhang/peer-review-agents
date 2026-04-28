from __future__ import annotations

import statistics
from typing import Any

from koala_strategy.discussion.claim_extractor import extract_claim
from koala_strategy.schemas import CommentRecord, DiscussionFeatureBundle
from koala_strategy.utils import safe_float


def extract_discussion_features_from_reviews(reviews: list[dict[str, Any]]) -> dict[str, float]:
    comments: list[CommentRecord] = []
    from datetime import datetime, timezone

    for idx, review in enumerate(reviews or []):
        text = "\n".join(str(review.get(k, "")) for k in ["summary", "strengths", "weaknesses", "questions"])
        comments.append(
            CommentRecord(
                comment_id=str(review.get("note_id") or f"review_{idx}"),
                paper_id="training_proxy",
                author_agent=str(review.get("signature") or f"reviewer_{idx}"),
                content_markdown=text,
                created_at=datetime.now(timezone.utc),
                quality_score=None,
            )
        )
    return extract_discussion_features("training_proxy", comments).features


def extract_discussion_features(paper_id: str, comments: list[CommentRecord]) -> DiscussionFeatureBundle:
    claims = [
        extract_claim(c.comment_id, c.author_agent, c.content_markdown, c.owner_id)
        for c in comments
    ]
    positive = [c for c in claims if c["polarity"] == "positive"]
    negative = [c for c in claims if c["polarity"] == "negative"]
    mixed = [c for c in claims if c["polarity"] == "mixed"]
    quality = [safe_float(c.get("quality_score"), 0.0) for c in claims]
    authors = {c["author_agent"] for c in claims}
    citable = [c for c in claims if c["quality_score"] >= 0.55]
    fatal = [c for c in claims if "fatal_flaw" in c["claim_types"]]
    missing_baseline = [c for c in claims if "missing_baseline" in c["claim_types"]]
    novelty = [c for c in claims if "novelty_prior_art" in c["claim_types"]]
    repro = [c for c in claims if "reproducibility_concern" in c["claim_types"]]
    features: dict[str, float | int] = {
        "positive_claim_count": len(positive),
        "negative_claim_count": len(negative),
        "mixed_claim_count": len(mixed),
        "max_fatal_flaw_severity": max([c["severity"] for c in fatal], default=0),
        "mean_fatal_flaw_severity": statistics.mean([c["severity"] for c in fatal]) if fatal else 0.0,
        "novelty_support_score": sum(1 for c in novelty if c["polarity"] == "positive"),
        "novelty_concern_score": sum(1 for c in novelty if c["polarity"] in {"negative", "mixed"}),
        "reproducibility_concern_score": len(repro),
        "missing_baseline_concern_score": len(missing_baseline),
        "theory_concern_score": sum("theorem" in c["summary"].lower() or "proof" in c["summary"].lower() for c in claims),
        "clarity_concern_score": sum("unclear" in c["summary"].lower() or "clarity" in c["summary"].lower() for c in claims),
        "independent_fatal_flaw_agreement": len({c["author_agent"] for c in fatal}),
        "independent_positive_agreement": len({c["author_agent"] for c in positive}),
        "review_confidence_mean": 0.0,
        "review_disagreement": 1.0 if positive and negative else 0.0,
        "comment_quality_mean": statistics.mean(quality) if quality else 0.0,
        "comment_quality_max": max(quality, default=0.0),
        "num_distinct_external_agents": len(authors),
        "num_citable_comments": len(citable),
    }
    return DiscussionFeatureBundle(paper_id=paper_id, features=features, extracted_claims=claims)

