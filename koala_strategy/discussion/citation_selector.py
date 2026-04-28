from __future__ import annotations

from collections import defaultdict

from koala_strategy.discussion.claim_extractor import extract_claim
from koala_strategy.schemas import CommentRecord


class InsufficientCitationsError(ValueError):
    pass


def select_citations(
    comments: list[CommentRecord],
    current_agent_name: str,
    same_owner_agent_names: set[str],
    min_citations: int = 3,
    max_citations: int = 5,
) -> list[CommentRecord]:
    valid: list[tuple[float, CommentRecord, dict]] = []
    seen_ids: set[str] = set()
    for comment in comments:
        if comment.comment_id in seen_ids:
            continue
        seen_ids.add(comment.comment_id)
        if comment.author_agent == current_agent_name:
            continue
        if comment.author_agent in same_owner_agent_names:
            continue
        claim = extract_claim(comment.comment_id, comment.author_agent, comment.content_markdown, comment.owner_id)
        quality = comment.quality_score if comment.quality_score is not None else claim["quality_score"]
        score = (
            1.2 * float(quality)
            + 0.8 * float(claim["decision_relevance"])
            + 0.4 * float(claim["specificity"])
        )
        valid.append((score, comment, claim))
    valid.sort(key=lambda x: x[0], reverse=True)
    by_author: dict[str, list[tuple[float, CommentRecord, dict]]] = defaultdict(list)
    for item in valid:
        by_author[item[1].author_agent].append(item)
    if len(by_author) < min_citations:
        raise InsufficientCitationsError(f"Need {min_citations} distinct external authors, found {len(by_author)}")

    selected: list[tuple[float, CommentRecord, dict]] = []
    polarities: set[str] = set()
    for author, items in sorted(by_author.items(), key=lambda kv: kv[1][0][0], reverse=True):
        selected.append(items[0])
        polarities.add(items[0][2]["polarity"])
        if len(selected) >= max_citations:
            break

    if not ({"positive", "negative"} <= polarities):
        for item in valid:
            if item in selected:
                continue
            if item[2]["polarity"] in {"positive", "negative"} and item[2]["polarity"] not in polarities:
                if len(selected) >= max_citations:
                    selected[-1] = item
                else:
                    selected.append(item)
                polarities.add(item[2]["polarity"])
                break
    selected = sorted(selected, key=lambda x: x[0], reverse=True)[:max_citations]
    if len({item[1].author_agent for item in selected}) < min_citations:
        raise InsufficientCitationsError("Selected citations do not cover enough distinct authors")
    return [item[1] for item in selected]

