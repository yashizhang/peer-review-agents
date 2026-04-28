from __future__ import annotations

from koala_strategy.schemas import CommentRecord, PaperRecord
from koala_strategy.discussion.citation_selector import select_citations


def can_comment(
    paper: PaperRecord,
    karma_remaining: float,
    already_commented_on_paper: bool = False,
    github_file_url_verified: bool = True,
    duplicate_content: bool = False,
) -> tuple[bool, str]:
    if paper.status != "in_review":
        return False, "comments are only allowed during in_review"
    if karma_remaining < (0.1 if already_commented_on_paper else 1.0):
        return False, "insufficient karma"
    if not github_file_url_verified:
        return False, "reasoning URL not verified"
    if duplicate_content:
        return False, "duplicate content hash"
    return True, "ok"


def can_submit_verdict(
    paper: PaperRecord,
    agent_name: str,
    agent_commented_in_review: bool,
    comments: list[CommentRecord],
    same_owner_agent_names: set[str],
    already_submitted: bool = False,
    min_citations: int = 3,
    min_comment_quality: float = 0.0,
) -> tuple[bool, str]:
    if paper.status != "deliberating":
        return False, "verdicts are only allowed during deliberating"
    if not agent_commented_in_review:
        return False, "agent did not comment during in_review"
    if already_submitted:
        return False, "verdict already submitted"
    try:
        select_citations(
            comments,
            agent_name,
            same_owner_agent_names,
            min_citations=min_citations,
            min_quality=min_comment_quality,
        )
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)
    return True, "ok"
