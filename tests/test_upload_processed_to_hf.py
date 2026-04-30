from __future__ import annotations

from pathlib import Path

from scripts.upload_processed_to_hf import _stage_filtered_subset


def test_stage_filtered_subset_copies_allowed_papers_and_metadata(tmp_path: Path) -> None:
    subset_root = tmp_path / "processed_papers" / "iclr26"
    keep = subset_root / "paper-keep"
    drop = subset_root / "paper-drop"
    keep.mkdir(parents=True)
    drop.mkdir()
    (keep / "model_text_v3.txt").write_text("keep", encoding="utf-8")
    (drop / "model_text_v3.txt").write_text("drop", encoding="utf-8")
    (subset_root / "_PARTIAL_SNAPSHOT.json").write_text('{"included_count":1}', encoding="utf-8")

    staged, count = _stage_filtered_subset(subset_root, {"paper-keep"}, tmp_path / "stage")

    assert count == 1
    assert (staged / "paper-keep" / "model_text_v3.txt").read_text(encoding="utf-8") == "keep"
    assert not (staged / "paper-drop").exists()
    assert (staged / "_PARTIAL_SNAPSHOT.json").exists()
