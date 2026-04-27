#!/usr/bin/env python3
"""Apply a previously fitted desk-reject calibration model to a new batch."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from iclr_desk_reject_model import evaluate_rows, load_rows, summarize_results


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--model-path",
        type=Path,
        default=Path("experimental/results/iclr_2026_desk_reject_calibrated/model.json"),
    )
    parser.add_argument(
        "--runs-dir",
        type=Path,
        required=True,
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    model = json.loads(args.model_path.read_text(encoding="utf-8"))
    rows = load_rows(args.runs_dir)
    results = evaluate_rows(rows, model)
    summary = summarize_results(
        results,
        note="This summary uses a model trained on a different split and applied out of sample.",
    )
    (args.output_dir / "results.jsonl").write_text(
        "\n".join(json.dumps(result, ensure_ascii=True) for result in results) + "\n",
        encoding="utf-8",
    )
    (args.output_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
