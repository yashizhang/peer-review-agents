from datetime import datetime, timezone

import pytest

from koala_strategy.discussion.citation_selector import InsufficientCitationsError, select_citations
from koala_strategy.schemas import CommentRecord


def c(comment_id, author, text, q=0.7):
    return CommentRecord(
        comment_id=comment_id,
        paper_id="p",
        author_agent=author,
        content_markdown=text,
        created_at=datetime.now(timezone.utc),
        quality_score=q,
    )


def test_filters_self_and_same_owner_and_returns_distinct_authors():
    comments = [
        c("self", "me", "Section 1 good", 0.99),
        c("same", "teammate", "Section 2 good", 0.99),
        c("a", "a", "Section 3 has strong positive evidence.", 0.8),
        c("b", "b", "Table 1 shows missing ablation concern.", 0.9),
        c("c", "c", "Figure 2 gives a clear but limited signal.", 0.7),
    ]
    selected = select_citations(comments, "me", {"teammate"})
    assert len(selected) == 3
    assert {x.author_agent for x in selected} == {"a", "b", "c"}


def test_fails_safely_if_insufficient():
    with pytest.raises(InsufficientCitationsError):
        select_citations([c("a", "a", "Section 1 good")], "me", set())


def test_quality_sorting():
    comments = [
        c("a", "a", "generic", 0.1),
        c("b", "b", "Table 2 contains a baseline concern.", 0.9),
        c("c", "c", "Section 4 has strong evidence.", 0.8),
        c("d", "d", "Figure 1 is useful.", 0.7),
    ]
    selected = select_citations(comments, "me", set(), min_citations=3, max_citations=3)
    assert "b" in [x.comment_id for x in selected]

