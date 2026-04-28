from datetime import datetime, timezone

from koala_strategy.agent.comment_writer import write_comment
from koala_strategy.agent.reasoning_writer import write_reasoning_file
from koala_strategy.agent.verdict_writer import prepare_verdict
from koala_strategy.schemas import CommentRecord, PaperRecord, PredictionBundle


def test_mock_comment_and_verdict_flow(tmp_path):
    paper = PaperRecord(paper_id="p", title="Test Paper", abstract="A method with ablations.", status="in_review", domains=["NLP"])
    bundle = PredictionBundle(
        paper_id="p",
        domain="NLP",
        model_version="v",
        paper_only={
            "p_accept": 0.61,
            "uncertainty": 0.2,
            "recommended_score_range": [5.4, 6.4],
            "main_positive_evidence": ["Table 1 reports a clear gain over a baseline."],
            "main_negative_evidence": ["The ablation evidence is limited."],
        },
        pseudo_review_panel={},
        agent_instruction={"comment_strategy": "balanced_borderline"},
    )
    content, evidence = write_comment(paper, bundle, "calibrated_decider")
    cfg = {"paths": {"reasoning_dir": str(tmp_path)}, "models": {}, "github": {}, "online_policy": {}, "agents": {}, "competition": {}}
    path = write_reasoning_file("calibrated_decider", "p", "comment", content, evidence, cfg)
    assert path.exists()

    paper.status = "deliberating"
    comments = [
        CommentRecord(comment_id="a", paper_id="p", author_agent="a", content_markdown="Section 4 has strong positive evidence.", created_at=datetime.now(timezone.utc), quality_score=0.8),
        CommentRecord(comment_id="b", paper_id="p", author_agent="b", content_markdown="Table 2 shows a missing baseline concern.", created_at=datetime.now(timezone.utc), quality_score=0.8),
        CommentRecord(comment_id="c", paper_id="p", author_agent="c", content_markdown="Section 2 novelty is plausible but incremental.", created_at=datetime.now(timezone.utc), quality_score=0.7),
    ]
    draft = prepare_verdict(paper, bundle, comments, "calibrated_decider")
    assert len(draft.citation_ids) >= 3
    assert "[[comment:" in draft.verdict_markdown

