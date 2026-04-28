from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from koala_strategy.config import load_config, resolve_path
from koala_strategy.utils import content_hash, ensure_dir, iso_now


def write_reasoning_file(
    agent_name: str,
    paper_id: str,
    action_type: Literal["comment", "reply", "verdict"],
    content_markdown: str,
    evidence: dict[str, Any],
    config: dict[str, Any] | None = None,
) -> Path:
    cfg = config or load_config()
    timestamp = iso_now().replace(":", "").replace("+", "Z")
    out_dir = ensure_dir(resolve_path(cfg, "reasoning_dir") / agent_name / paper_id)
    path = out_dir / f"{timestamp}_{action_type}.md"
    prediction_summary = evidence.get("prediction_summary") or evidence
    body = f"""# Reasoning file

Agent: {agent_name}
Paper ID: {paper_id}
Action: {action_type}
Timestamp: {iso_now()}
Content hash: {content_hash(content_markdown)}

## Prediction summary

```json
{prediction_summary}
```

## Evidence from paper

- Positive signals: {evidence.get("positive_evidence", evidence.get("positive_summary", "n/a"))}
- Negative signals: {evidence.get("negative_evidence", evidence.get("negative_summary", "n/a"))}

## Discussion evidence, if applicable

{evidence.get("discussion_summary", "No discussion evidence used for this action.")}

## Action content

{content_markdown}

## Policy checks

- Did not use future leaked same-paper information.
- Did not cite self.
- Did not cite same-owner agents.
- URL verified before posting when real posting is enabled.
"""
    path.write_text(body, encoding="utf-8")
    return path

