from __future__ import annotations

from koala_strategy.discussion.claim_extractor import extract_claim


def score_comment_quality(comment_id: str, author_agent: str, content: str) -> float:
    return float(extract_claim(comment_id, author_agent, content)["quality_score"])

