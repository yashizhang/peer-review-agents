from datetime import datetime, timezone

from koala_strategy.agent.lifecycle_policy import can_comment, can_submit_verdict
from koala_strategy.schemas import CommentRecord, PaperRecord


def comment(i, author):
    return CommentRecord(comment_id=i, paper_id="p", author_agent=author, content_markdown="Section 1 has decision-relevant evidence.", created_at=datetime.now(timezone.utc), quality_score=0.7)


def test_cannot_comment_outside_in_review():
    paper = PaperRecord(paper_id="p", title="t", status="deliberating")
    ok, _ = can_comment(paper, 100)
    assert not ok


def test_cannot_verdict_unless_deliberating():
    paper = PaperRecord(paper_id="p", title="t", status="in_review")
    ok, _ = can_submit_verdict(paper, "me", True, [comment("a", "a"), comment("b", "b"), comment("c", "c")], set())
    assert not ok


def test_cannot_verdict_without_prior_comment():
    paper = PaperRecord(paper_id="p", title="t", status="deliberating")
    ok, _ = can_submit_verdict(paper, "me", False, [comment("a", "a"), comment("b", "b"), comment("c", "c")], set())
    assert not ok


def test_cannot_verdict_without_three_valid_citations():
    paper = PaperRecord(paper_id="p", title="t", status="deliberating")
    ok, _ = can_submit_verdict(paper, "me", True, [comment("a", "a")], set())
    assert not ok

