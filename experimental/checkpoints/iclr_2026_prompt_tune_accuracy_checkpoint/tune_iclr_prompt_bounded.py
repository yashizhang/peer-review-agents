#!/usr/bin/env python3
"""Run a bounded prompt-only tuning sweep for the ICLR desk-reject agent."""

from __future__ import annotations

import argparse
import json
import subprocess
import shutil
import sys
from pathlib import Path

from backtest_iclr_desk_reject import load_existing_runs, run_one_existing, summarize

VARIANT_FILES = [
    "v1_harness_aligned.md",
    "v2_page_conservative.md",
    "v3_integrity_conservative.md",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--input-root",
        type=Path,
        default=Path("experimental/tmp_iclr_pipeline_full/prompt_runs"),
        help="Root containing train/val/test subdirs with runs/ children.",
    )
    parser.add_argument(
        "--variants-dir",
        type=Path,
        default=Path("agent_configs/iclr-desk-reject/variants"),
    )
    parser.add_argument(
        "--results-dir",
        type=Path,
        default=Path("experimental/results/iclr_2026_prompt_tune_bounded"),
    )
    parser.add_argument(
        "--baseline-summary",
        type=Path,
        default=Path("experimental/results/iclr_2026_pipeline_improved/prompt_only_summaries.json"),
    )
    parser.add_argument(
        "--selected-test-dir",
        type=Path,
        default=Path("experimental/results/iclr_2026_prompt_tune_bounded/selected/test"),
        help="Output directory for the frozen selected-prompt test run.",
    )
    parser.add_argument("--model", default="gpt-5.4")
    parser.add_argument("--clean", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def run_variant_split(
    input_runs_dir: Path,
    output_dir: Path,
    prompt_path: Path,
    model: str,
    overwrite: bool,
) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = load_existing_runs(input_runs_dir)
    results = []
    for row in rows:
        print(f"[{prompt_path.stem}] {input_runs_dir.parent.name}: {row['forum_id']} ({row['label']})", flush=True)
        results.append(
            run_one_existing(
                row=row,
                agent_prompt_path=prompt_path,
                output_dir=output_dir,
                model=model,
                overwrite=overwrite,
            )
        )
    summary = summarize(results)
    (output_dir / "results.jsonl").write_text(
        "\n".join(json.dumps(item, ensure_ascii=True) for item in results) + "\n",
        encoding="utf-8",
    )
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary


def selection_tuple(val_summary: dict, prompt_text: str) -> tuple[float, int, float, int]:
    return (
        val_summary["strict"]["accuracy"],
        -val_summary["strict"]["fp"],
        val_summary["lenient"]["accuracy"],
        -len(prompt_text),
    )


def run_selected_test(
    input_runs_dir: Path,
    output_dir: Path,
    prompt_path: Path,
    model: str,
    overwrite: bool,
) -> dict:
    summary_path = output_dir / "summary.json"
    if summary_path.exists() and not overwrite:
        return json.loads(summary_path.read_text(encoding="utf-8"))

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    script_path = Path(__file__).with_name("backtest_iclr_desk_reject.py")
    cmd = [
        sys.executable,
        str(script_path),
        "--input-runs-dir",
        str(input_runs_dir),
        "--agent-prompt",
        str(prompt_path),
        "--output-dir",
        str(output_dir),
        "--model",
        model,
    ]
    if overwrite:
        cmd.append("--overwrite")
    completed = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError(
            f"selected test run failed with exit code {completed.returncode}\n"
            f"stdout:\n{completed.stdout}\n\nstderr:\n{completed.stderr}"
        )
    return json.loads(summary_path.read_text(encoding="utf-8"))


def main() -> None:
    args = parse_args()
    if args.clean and args.results_dir.exists():
        shutil.rmtree(args.results_dir)
    args.results_dir.mkdir(parents=True, exist_ok=True)

    candidates = []
    for filename in VARIANT_FILES:
        prompt_path = args.variants_dir / filename
        prompt_text = prompt_path.read_text(encoding="utf-8")
        candidate_root = args.results_dir / "candidates" / prompt_path.stem
        train_summary = run_variant_split(
            input_runs_dir=args.input_root / "train" / "runs",
            output_dir=candidate_root / "train",
            prompt_path=prompt_path,
            model=args.model,
            overwrite=args.overwrite,
        )
        val_summary = run_variant_split(
            input_runs_dir=args.input_root / "val" / "runs",
            output_dir=candidate_root / "val",
            prompt_path=prompt_path,
            model=args.model,
            overwrite=args.overwrite,
        )
        candidate = {
            "variant": prompt_path.stem,
            "prompt_path": str(prompt_path),
            "prompt_length": len(prompt_text),
            "train": train_summary,
            "val": val_summary,
        }
        candidates.append(candidate)

    selected = max(
        candidates,
        key=lambda item: (
            item["val"]["strict"]["accuracy"],
            -item["val"]["strict"]["fp"],
            item["val"]["lenient"]["accuracy"],
            -item["prompt_length"],
        ),
    )

    selected_prompt_path = Path(selected["prompt_path"])
    test_summary = run_selected_test(
        input_runs_dir=args.input_root / "test" / "runs",
        output_dir=args.selected_test_dir,
        prompt_path=selected_prompt_path,
        model=args.model,
        overwrite=args.overwrite,
    )

    frozen_dir = args.results_dir / "selected"
    shutil.copyfile(selected_prompt_path, frozen_dir / "system_prompt.md")

    baseline = json.loads(args.baseline_summary.read_text(encoding="utf-8"))
    comparison = {
        "val": {
            "baseline": baseline["val"],
            "candidate": selected["val"],
            "strict_delta": selected["val"]["strict"]["accuracy"] - baseline["val"]["strict"]["accuracy"],
            "lenient_delta": selected["val"]["lenient"]["accuracy"] - baseline["val"]["lenient"]["accuracy"],
        },
        "test": {
            "baseline": baseline["test"],
            "candidate": test_summary,
            "strict_delta": test_summary["strict"]["accuracy"] - baseline["test"]["strict"]["accuracy"],
            "lenient_delta": test_summary["lenient"]["accuracy"] - baseline["test"]["lenient"]["accuracy"],
        },
    }

    artifacts = {
        "candidates": candidates,
        "selected_variant": selected["variant"],
        "selected_prompt_path": str(selected_prompt_path),
        "test": test_summary,
        "comparison": comparison,
    }
    (args.results_dir / "selection_summary.json").write_text(
        json.dumps(artifacts, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(artifacts, indent=2))


if __name__ == "__main__":
    main()
