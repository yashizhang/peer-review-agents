import json
from pathlib import Path


def test_axis_panel_agent_files_exist():
    root = Path(__file__).resolve().parents[1]
    agent_dir = root / "agent_configs" / "axis-panel-master"

    assert (agent_dir / "system_prompt.md").is_file()
    assert (agent_dir / "config.json").is_file()
    cfg = json.loads((agent_dir / "config.json").read_text())
    assert cfg["backend"] == "codex"
    assert (agent_dir / ".agent_name").read_text().strip() == "axis-panel-master"
    assert not (agent_dir / ".api_key").exists(), "API keys must not be committed"


def test_axis_panel_prompt_contains_all_review_axes():
    root = Path(__file__).resolve().parents[1]
    prompt = (root / "agent_configs" / "axis-panel-master" / "system_prompt.md").read_text()

    required_phrases = [
        "Evidence Completeness Agent",
        "Clarity and Reproducibility Agent",
        "Practical Scope Agent",
        "Technical Soundness Agent",
        "Novelty and Positioning Agent",
        "Master synthesis",
        "reasoning/axis-panel-master/<paper_id>/",
    ]
    for phrase in required_phrases:
        assert phrase in prompt
