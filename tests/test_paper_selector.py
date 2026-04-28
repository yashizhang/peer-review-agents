from koala_strategy.agent.paper_selector import should_comment
from koala_strategy.agent.sharding import assigned_agent
from koala_strategy.schemas import PaperRecord, PredictionBundle


def bundle(paper_id="p"):
    return PredictionBundle(
        paper_id=paper_id,
        model_version="v",
        paper_only={"main_positive_evidence": ["x"], "main_negative_evidence": ["y"], "p_accept": 0.7, "uncertainty": 0.2},
        pseudo_review_panel={},
        agent_instruction={},
    )


def test_rejects_over_commented_paper():
    paper = PaperRecord(paper_id="p", title="t", status="in_review", comment_count=99)
    ok, _ = should_comment(paper, bundle(), assigned_agent("p", ["a", "b"]), agents=["a", "b"], config={"online_policy": {"skip_if_comment_count_too_high": 18}})
    assert not ok


def test_rejects_wrong_shard():
    agents = ["a", "b", "c"]
    paper = PaperRecord(paper_id="p", title="t", status="in_review")
    owner = assigned_agent("p", agents)
    wrong = next(a for a in agents if a != owner)
    ok, reason = should_comment(paper, bundle(), wrong, agents=agents)
    assert not ok
    assert "shard" in reason


def test_accepts_viable_paper_on_shard():
    agents = ["a", "b", "c"]
    paper = PaperRecord(paper_id="p", title="t", status="in_review", participant_count=3)
    owner = assigned_agent("p", agents)
    ok, _ = should_comment(paper, bundle(), owner, agents=agents)
    assert ok

