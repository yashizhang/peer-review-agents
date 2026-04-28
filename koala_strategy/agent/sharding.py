from __future__ import annotations

from koala_strategy.utils import stable_hash


def assigned_agent(paper_id: str, agents: list[str]) -> str:
    if not agents:
        raise ValueError("agents cannot be empty")
    return agents[stable_hash(paper_id) % len(agents)]

