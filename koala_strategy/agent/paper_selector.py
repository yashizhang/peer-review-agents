from __future__ import annotations

from typing import Any

from koala_strategy.agent.sharding import assigned_agent
from koala_strategy.constants import AGENT_NAMES
from koala_strategy.schemas import PaperRecord, PredictionBundle


def compute_paper_utility(
    paper: PaperRecord,
    prediction: PredictionBundle | None,
    agent_name: str,
    agent_focus: str,
    current_karma: float,
) -> float:
    p = prediction.paper_only if prediction else {}
    p_accept = float(p.get("p_accept", 0.5))
    uncertainty = float(p.get("uncertainty", 0.6))
    predicted_metric_gain = abs(p_accept - 0.5) * 2.0 * (1.0 - uncertainty)
    coverage_reward = 0.5 if paper.domains else 0.0
    if 2 <= paper.participant_count <= 9:
        under_reviewed_bonus = 1.0
    elif paper.participant_count > 12:
        under_reviewed_bonus = -0.8
    else:
        under_reviewed_bonus = 0.2
    citation_probability = min(1.0, paper.participant_count / 4.0)
    cannot_get_3_citations_risk = 1.0 if paper.participant_count < 3 else 0.0
    time_cost = 0.1
    return (
        1.8 * predicted_metric_gain
        + 1.0 * coverage_reward
        + 0.8 * under_reviewed_bonus
        + 0.6 * citation_probability
        - 0.7 * uncertainty
        - 0.4 * time_cost
        - 2.0 * cannot_get_3_citations_risk
    )


def should_comment(
    paper: PaperRecord,
    prediction: PredictionBundle | None,
    agent_name: str,
    already_commented_by_agent: bool = False,
    current_karma: float = 100.0,
    agents: list[str] | None = None,
    paper_age_hours: float | None = None,
    config: dict[str, Any] | None = None,
) -> tuple[bool, str]:
    policy = (config or {}).get("online_policy", {}) if config else {}
    if paper.status != "in_review":
        return False, "paper is not in_review"
    if already_commented_by_agent:
        return False, "agent already commented"
    if current_karma < 1.0:
        return False, "insufficient karma"
    if paper_age_hours is not None:
        if paper_age_hours > float(policy.get("max_paper_age_hours_to_comment", 44)):
            return False, "paper too old for comment"
        if paper_age_hours < float(policy.get("min_paper_age_hours_to_comment", 4)) and paper.participant_count < 1:
            return False, "paper too new and no discussion yet"
    if paper.comment_count > int(policy.get("skip_if_comment_count_too_high", 18)):
        return False, "discussion already crowded"
    roster = agents or AGENT_NAMES
    if assigned_agent(paper.paper_id, roster) != agent_name:
        return False, "paper assigned to another agent shard"
    if prediction:
        evidence = prediction.paper_only.get("main_positive_evidence", []) + prediction.paper_only.get("main_negative_evidence", [])
        if not any(str(item).strip() for item in evidence):
            return False, "no substantive evidence generated"
    return True, "ok"

