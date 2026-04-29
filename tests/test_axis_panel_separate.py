import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_axis_panel_separate_files_exist():
    root = Path(__file__).resolve().parents[1]
    agent_dir = root / "agent_configs" / "axis-panel-separate"

    assert (agent_dir / "system_prompt.md").is_file()
    assert (agent_dir / "calibrator_prompt.md").is_file()
    assert (agent_dir / "config.json").is_file()
    cfg = json.loads((agent_dir / "config.json").read_text())
    assert cfg["backend"] == "codex"
    assert (agent_dir / ".agent_name").read_text().strip() == "axis-panel-separate"


def test_axis_panel_separate_prompt_keeps_main_review_paper_only():
    root = Path(__file__).resolve().parents[1]
    prompt = (root / "agent_configs" / "axis-panel-separate" / "system_prompt.md").read_text()
    assert "Do not use review memory or priors during the main paper review." in prompt
    assert "Final public-facing text must cite only target-paper evidence" in prompt
    assert "Live execution protocol" in prompt
    assert "review_priors_by_category.json" in prompt
    assert "calibrator_prompt.md" in prompt
    assert "score threshold `6.4`" in prompt


def test_separate_priors_builder_filters_official_reviews_and_eval_ids():
    from experimental.build_iclr2026_review_priors import build_priors

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        reviews_path = tmp_path / "reviews.jsonl"
        papers_path = tmp_path / "papers.jsonl"
        manifest_path = tmp_path / "manifest.jsonl"
        papers_path.write_text(
            "\n".join(
                [
                    json.dumps(
                        {
                            "id": "keep-paper",
                            "title": "Keep",
                            "abstract": "A",
                            "status": "accepted",
                            "koala_or_fallback_categories": ["d/NLP"],
                        }
                    ),
                    json.dumps(
                        {
                            "id": "eval-paper",
                            "title": "Eval",
                            "abstract": "B",
                            "status": "rejected",
                            "koala_or_fallback_categories": ["d/NLP"],
                        }
                    ),
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        reviews_path.write_text(
            "\n".join(
                [
                    json.dumps(
                        {
                            "kind": "official_review",
                            "paper_id": "keep-paper",
                            "note_id": "r1",
                            "content": {
                                "rating": 6,
                                "summary": "Strong experiments and baseline comparisons.",
                                "strengths": "Clear comparison with strong baselines.",
                                "weaknesses": "Needs more ablations.",
                                "questions": "Can you add more ablations?",
                            },
                        }
                    ),
                    json.dumps(
                        {
                            "kind": "comment",
                            "paper_id": "keep-paper",
                            "note_id": "c1",
                            "content": {"summary": "Ignore me."},
                        }
                    ),
                    json.dumps(
                        {
                            "kind": "official_review",
                            "paper_id": "eval-paper",
                            "note_id": "r2",
                            "content": {
                                "rating": 2,
                                "summary": "Should be excluded by eval set.",
                                "weaknesses": "Weak novelty.",
                            },
                        }
                    ),
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        manifest_path.write_text(json.dumps({"forum_id": "eval-paper", "title": "Eval", "status": "rejected"}) + "\n")
        priors = build_priors(
            reviews_path=reviews_path,
            papers_path=papers_path,
            manifest=manifest_path,
        )
        assert priors["excluded_eval_forum_count"] == 1
        assert priors["official_reviews_admitted"] == 1
        assert "d/NLP" in priors["categories"]


def test_separate_calibrator_enforces_bounded_delta_and_band_step():
    from experimental.review_calibrator import apply_calibration

    fused = apply_calibration(
        base_result={"score": 4.9, "decision_band": "weak reject", "confidence": "medium"},
        calibration={
            "delta_score": 2.0,
            "suggested_decision_band": "spotlight",
            "confidence": "high",
            "use_priors": True,
            "calibration_note": "Over-aggressive suggestion.",
        },
    )
    assert fused["delta_score_applied"] == 0.5
    assert fused["decision_band"] == "weak accept"


def test_separate_backtest_schema_mentions_base_fields():
    root = Path(__file__).resolve().parents[1]
    script = (root / "experimental" / "backtest_axis_panel_separate.py").read_text()
    assert "base_result.json" in script
    assert "calibration_note" in script
    assert '"memory_used"' in script
    assert '"score_delta"' in script
    assert '"score_predicted_accept"' in script
    assert 'score_accepts(final_score, threshold=FIXED_THRESHOLD)' in script
