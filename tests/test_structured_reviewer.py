from __future__ import annotations

import random
import json
import threading
import time
from pathlib import Path

from koala_strategy.llm import structured_reviewer


class FakeProvider:
    def __init__(self, content: str):
        self.content = content
        self.calls = 0

    def generate(self, prompt: str, *, model: str | None = None, temperature: float = 0.0) -> str:
        self.calls += 1
        return self.content


class ConcurrentFakeProvider:
    def __init__(self, content: str):
        self.content = content
        self.calls = 0
        self.in_flight = 0
        self.max_in_flight = 0
        self.lock = threading.Lock()

    def generate(self, prompt: str, *, model: str | None = None, temperature: float = 0.0) -> str:
        with self.lock:
            self.calls += 1
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
        time.sleep(0.02)
        with self.lock:
            self.in_flight -= 1
        return self.content


def _paper_dir(tmp_path: Path, paper_id: str = "p1") -> Path:
    root = tmp_path / "processed_papers" / "iclr26" / paper_id
    root.mkdir(parents=True)
    (root / "model_text_v3.txt").write_text("Abstract\nA method with experiments and ablations.", encoding="utf-8")
    (root / "dataset_meta.json").write_text(json.dumps({"paper_id": paper_id, "source_status": "accepted"}), encoding="utf-8")
    return root


def test_self_review_prompt_excludes_training_only_fields(tmp_path: Path) -> None:
    paper_dir = _paper_dir(tmp_path)

    prompt = structured_reviewer.build_self_review_prompt(
        paper_id="p1",
        title="Test Paper",
        domains=["NLP"],
        paper_text=structured_reviewer.read_processed_paper_text(paper_dir),
    )

    assert "official_reviews" not in prompt
    assert "decision_label" not in prompt
    assert "accept_label" not in prompt
    assert "source_status" not in prompt
    assert "accepted" not in prompt.lower()


def test_validate_self_review_clamps_axes() -> None:
    review = structured_reviewer.validate_self_review(
        {
            "axes": {
                "empirical_validation": {"score": 12, "risk": -1, "confidence": 3},
            },
            "overall_accept_probability": 2.0,
        },
        paper_id="p1",
        model="deepseek-v4-pro",
    )

    axis = review["axes"]["empirical_validation"]
    assert axis["score"] == 10.0
    assert axis["risk"] == 0.0
    assert axis["confidence"] == 1.0
    assert review["overall_accept_probability"] == 1.0
    assert set(review["axes"]) == set(structured_reviewer.REVIEW_AXES)


def test_extract_self_review_uses_cache(tmp_path: Path) -> None:
    paper_dir = _paper_dir(tmp_path)
    provider = FakeProvider(
        json.dumps(
            {
                "axes": {
                    axis: {"score": 6, "risk": 0.3, "confidence": 0.7, "rationale": "ok"}
                    for axis in structured_reviewer.REVIEW_AXES
                },
                "overall_accept_probability": 0.6,
            }
        )
    )

    first = structured_reviewer.extract_self_review_for_paper(
        paper_dir,
        provider=provider,
        cache_dir=tmp_path / "cache",
        title="Test Paper",
    )
    second = structured_reviewer.extract_self_review_for_paper(
        paper_dir,
        provider=provider,
        cache_dir=tmp_path / "cache",
        title="Test Paper",
    )

    assert provider.calls == 1
    assert first == second
    assert first["paper_id"] == "p1"
    assert (tmp_path / "cache" / "p1.json").exists()


def test_flatten_self_review_features() -> None:
    review = structured_reviewer.validate_self_review({"axes": {}}, paper_id="p1", model="m")

    flat = structured_reviewer.flatten_self_review_features(review)

    assert flat["self_empirical_validation_score"] == 5.0
    assert flat["self_empirical_validation_risk"] == 0.5
    assert flat["self_overall_accept_probability"] == 0.5


def test_extract_self_review_features_preserves_split(monkeypatch, tmp_path: Path) -> None:
    _paper_dir(tmp_path, paper_id="train_paper")
    _paper_dir(tmp_path, paper_id="test_paper")
    provider = FakeProvider(
        json.dumps(
            {
                "axes": {
                    axis: {"score": 6, "risk": 0.3, "confidence": 0.7}
                    for axis in structured_reviewer.REVIEW_AXES
                },
                "overall_accept_probability": 0.6,
            }
        )
    )
    monkeypatch.setattr(
        structured_reviewer,
        "_load_iclr_metadata",
        lambda cfg: {
            "train_paper": {"split": "train", "accept_label": 1, "title": "Train"},
            "test_paper": {"split": "test", "accept_label": 0, "title": "Test"},
        },
    )
    output_path = tmp_path / "features.jsonl"

    structured_reviewer.extract_self_review_features(
        processed_root=tmp_path / "processed_papers" / "iclr26",
        output_path=output_path,
        cache_dir=tmp_path / "cache",
        provider=provider,
        config={},
    )

    rows = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines()]

    assert rows[0]["paper_id"] == "test_paper"
    assert rows[0]["split"] == "test"
    assert rows[1]["paper_id"] == "train_paper"
    assert rows[1]["split"] == "train"


def test_extract_self_review_features_runs_workers_concurrently(monkeypatch, tmp_path: Path) -> None:
    for idx in range(4):
        _paper_dir(tmp_path, paper_id=f"p{idx}")
    provider = ConcurrentFakeProvider(
        json.dumps(
            {
                "axes": {
                    axis: {"score": 6, "risk": 0.3, "confidence": 0.7}
                    for axis in structured_reviewer.REVIEW_AXES
                },
                "overall_accept_probability": 0.6,
            }
        )
    )
    monkeypatch.setattr(
        structured_reviewer,
        "_load_iclr_metadata",
        lambda cfg: {f"p{idx}": {"split": "train", "accept_label": idx % 2} for idx in range(4)},
    )
    output_path = tmp_path / "features.jsonl"

    structured_reviewer.extract_self_review_features(
        processed_root=tmp_path / "processed_papers" / "iclr26",
        output_path=output_path,
        cache_dir=tmp_path / "cache",
        workers=2,
        provider=provider,
        config={},
    )

    rows = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines()]

    assert provider.calls == 4
    assert provider.max_in_flight >= 2
    assert [row["paper_id"] for row in rows] == ["p0", "p1", "p2", "p3"]


def test_extract_self_review_features_can_shuffle_papers(monkeypatch, tmp_path: Path) -> None:
    for paper_id in ["p0", "p1", "p2"]:
        _paper_dir(tmp_path, paper_id=paper_id)
    provider = FakeProvider(
        json.dumps(
            {
                "axes": {
                    axis: {"score": 6, "risk": 0.3, "confidence": 0.7}
                    for axis in structured_reviewer.REVIEW_AXES
                },
                "overall_accept_probability": 0.6,
            }
        )
    )
    monkeypatch.setattr(
        structured_reviewer,
        "_load_iclr_metadata",
        lambda cfg: {},
    )
    output_path = tmp_path / "features.jsonl"
    ordered = [
        tmp_path / "processed_papers" / "iclr26" / "p0",
        tmp_path / "processed_papers" / "iclr26" / "p1",
        tmp_path / "processed_papers" / "iclr26" / "p2",
    ]
    random.Random(7).shuffle(ordered)

    structured_reviewer.extract_self_review_features(
        processed_root=tmp_path / "processed_papers" / "iclr26",
        output_path=output_path,
        cache_dir=tmp_path / "cache",
        workers=1,
        shuffle=True,
        seed=7,
        provider=provider,
        config={},
    )

    rows = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines()]
    assert [row["paper_id"] for row in rows] == [p.name for p in ordered]
