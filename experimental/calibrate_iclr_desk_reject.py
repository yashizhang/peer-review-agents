#!/usr/bin/env python3
"""Fit a calibration model and optionally evaluate it on a chosen split."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from iclr_desk_reject_model import evaluate_rows, fit_bernoulli_nb, load_rows, summarize_results


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--train-runs-dir",
        type=Path,
        default=Path("experimental/results/iclr_2026_desk_reject_backtest/runs"),
    )
    parser.add_argument(
        "--eval-runs-dir",
        type=Path,
        default=None,
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("experimental/results/iclr_2026_desk_reject_calibrated"),
    )
    parser.add_argument("--min-df", type=int, default=2)
    parser.add_argument(
        "--summary-note",
        default="This summary is in-sample on the tuned batch, not a held-out estimate.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    train_rows = load_rows(args.train_runs_dir)
    eval_rows = load_rows(args.eval_runs_dir or args.train_runs_dir)
    model = fit_bernoulli_nb(train_rows, min_df=args.min_df)
    results = evaluate_rows(eval_rows, model)
    summary = summarize_results(results, note=args.summary_note)
    (args.output_dir / "model.json").write_text(json.dumps(model, indent=2) + "\n", encoding="utf-8")
    (args.output_dir / "results.jsonl").write_text(
        "\n".join(json.dumps(result, ensure_ascii=True) for result in results) + "\n",
        encoding="utf-8",
    )
    (args.output_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
