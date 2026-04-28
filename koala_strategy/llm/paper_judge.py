from __future__ import annotations

from koala_strategy.llm.pseudo_review_panel import aggregate_pseudo_reviews, run_pseudo_review_panel
from koala_strategy.schemas import ParsedPaperText


def judge_paper(parsed: ParsedPaperText) -> dict[str, float]:
    return aggregate_pseudo_reviews(run_pseudo_review_panel(parsed))

