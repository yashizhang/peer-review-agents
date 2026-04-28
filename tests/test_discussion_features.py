from datetime import datetime, timezone

from koala_strategy.discussion.discussion_features import extract_discussion_features
from koala_strategy.schemas import CommentRecord


def test_discussion_features_counts_signals():
    comments = [
        CommentRecord(comment_id="a", paper_id="p", author_agent="a", content_markdown="Section 4 has strong positive evidence.", created_at=datetime.now(timezone.utc)),
        CommentRecord(comment_id="b", paper_id="p", author_agent="b", content_markdown="Table 2 shows a missing baseline concern.", created_at=datetime.now(timezone.utc)),
    ]
    bundle = extract_discussion_features("p", comments)
    assert bundle.features["positive_claim_count"] >= 1
    assert bundle.features["negative_claim_count"] >= 1
    assert bundle.features["num_distinct_external_agents"] == 2

