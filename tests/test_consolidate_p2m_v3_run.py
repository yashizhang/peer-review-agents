from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_consolidate_writes_subset_under_dataset_root(tmp_path: Path) -> None:
    run_root = tmp_path / "processed_papers"
    paper_dir = run_root / "run_a" / "processed_v3" / "paper-1"
    paper_dir.mkdir(parents=True)
    (paper_dir / "model_text_v3.txt").write_text("body", encoding="utf-8")
    (paper_dir / "sanitization_report.json").write_text(
        json.dumps({"ok": True}) + "\n",
        encoding="utf-8",
    )
    (run_root / "run_a" / "input_manifest.json").write_text(
        json.dumps([{"paper_id": "paper-1", "source": "icml-source"}]) + "\n",
        encoding="utf-8",
    )

    subprocess.run(
        [
            sys.executable,
            "scripts/jobs/consolidate_p2m_v3_run.py",
            "--run-root",
            str(run_root),
            "--run-name",
            "run_a",
            "--subset",
            "icml26",
        ],
        check=True,
        cwd=Path(__file__).resolve().parents[1],
    )

    target = run_root / "icml26" / "paper-1"
    assert (target / "model_text_v3.txt").read_text(encoding="utf-8") == "body"
    assert not (tmp_path / "icml26").exists()
    metadata = json.loads((target / "dataset_meta.json").read_text(encoding="utf-8"))
    assert metadata["paper_id"] == "paper-1"
    assert metadata["run_name"] == "run_a"
    assert metadata["source"] == "icml-source"
