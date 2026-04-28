from __future__ import annotations

from datetime import datetime, timezone

from koala_strategy.llm.agentic_reviewer import (
    compose_llm_public_comment,
    prepare_agentic_verdict,
    run_llm_candidate_pool_planner,
    run_llm_paper_triage,
    synthesize_discussion_with_llm,
)
from koala_strategy.schemas import CommentRecord, PaperRecord, PredictionBundle


def _cfg() -> dict:
    return {
        "models": {
            "llm_provider": "heuristic",
            "agentic_llm": {"enabled": True},
        },
        "online_policy": {"min_comment_quality_to_cite": 0.55},
        "competition": {"min_external_citations_for_verdict": 3},
    }


def _paper(status: str = "in_review") -> PaperRecord:
    return PaperRecord(
        paper_id="p_agentic",
        title="Agentic Test Paper",
        abstract="We propose a method and report improvements with ablations.",
        status=status,
        comment_count=2,
        participant_count=3,
    )


def _prediction() -> PredictionBundle:
    return PredictionBundle(
        paper_id="p_agentic",
        model_version="test",
        paper_only={
            "p_accept": 0.61,
            "uncertainty": 0.31,
            "recommended_score_range": [5.4, 6.4],
            "main_positive_evidence": ["The paper reports an improvement over a baseline."],
            "main_negative_evidence": ["The ablation does not fully isolate each proposed component."],
        },
        pseudo_review_panel={"reviewer_component_summary": []},
        agent_instruction={},
    )


def _comments() -> list[CommentRecord]:
    now = datetime.now(timezone.utc)
    return [
        CommentRecord(
            comment_id=f"c{i}",
            paper_id="p_agentic",
            author_agent=f"external_{i}",
            content_markdown="This is a specific decision-relevant comment about baselines, ablations, scope, and claim support.",
            created_at=now,
            quality_score=0.8,
        )
        for i in range(3)
    ]


def test_agentic_fallback_comment_and_planner() -> None:
    cfg = _cfg()
    paper = _paper()
    prediction = _prediction()
    rows = [{"paper": paper, "prediction": prediction, "comments": [], "utility": 2.0}]
    plans = run_llm_candidate_pool_planner(rows, "review_director", cfg)
    assert paper.paper_id in plans
    triage = run_llm_paper_triage(paper, prediction, [], "review_director", 2.0, cfg)
    content, evidence = compose_llm_public_comment(paper, prediction, {}, triage, "review_director", cfg)
    assert "Decision-relevant" in content
    assert evidence["agentic_llm_comment"] is True


def test_agentic_fallback_verdict_has_required_refs() -> None:
    cfg = _cfg()
    paper = _paper(status="deliberating")
    prediction = _prediction()
    comments = _comments()
    synthesis = synthesize_discussion_with_llm(
        paper,
        prediction,
        comments,
        "review_director",
        set(),
        {},
        {},
        cfg,
        min_comment_quality=0.55,
        min_citations=3,
    )
    draft = prepare_agentic_verdict(paper, prediction, comments, "review_director", set(), {}, synthesis, {}, cfg)
    assert len(set(draft.citation_ids)) >= 3
    for cid in draft.citation_ids:
        assert f"[[comment:{cid}]]" in draft.verdict_markdown
    assert 0.0 <= draft.score <= 10.0
