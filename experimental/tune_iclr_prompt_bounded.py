#!/usr/bin/env python3
"""Run a bounded prompt-only tuning sweep for the ICLR desk-reject agent."""

from __future__ import annotations

import argparse
import json
import subprocess
import shutil
import sys
from pathlib import Path

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
        default=Path("experimental/results/iclr_2026_prompt_tune_recall_guarded"),
    )
    parser.add_argument(
        "--baseline-summary",
        type=Path,
        default=Path("experimental/checkpoints/iclr_2026_prompt_tune_accuracy_checkpoint/selection_summary.json"),
    )
    parser.add_argument(
        "--selected-test-dir",
        type=Path,
        default=Path("experimental/results/iclr_2026_prompt_tune_recall_guarded/selected/test"),
        help="Output directory for the frozen selected-prompt test run.",
    )
    parser.add_argument(
        "--strict-precision-floor",
        type=float,
        default=0.9,
        help="Minimum strict precision required on validation before recall becomes the primary objective.",
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
            f"split run failed for {prompt_path.stem} on {input_runs_dir} with exit code {completed.returncode}\n"
            f"stdout:\n{completed.stdout}\n\nstderr:\n{completed.stderr}"
        )
    return json.loads(summary_path.read_text(encoding="utf-8"))


def strict_metrics(summary: dict) -> dict:
    strict = summary["strict"]
    tp = strict["tp"]
    fp = strict["fp"]
    fn = strict["fn"]
    predicted_positive = tp + fp
    actual_positive = tp + fn
    precision = tp / predicted_positive if predicted_positive else 1.0
    recall = tp / actual_positive if actual_positive else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return {
        "accuracy": strict["accuracy"],
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "predicted_positive": predicted_positive,
        "actual_positive": actual_positive,
    }


def load_baseline(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if "candidates" in payload and "selected_variant" in payload:
        selected_variant = payload["selected_variant"]
        selected_candidate = next(item for item in payload["candidates"] if item["variant"] == selected_variant)
        return {
            "train": selected_candidate["train"],
            "val": selected_candidate["val"],
            "test": payload["test"],
            "selected_variant": selected_variant,
        }
    return payload


def pick_candidate(candidates: list[dict], precision_floor: float) -> tuple[dict, dict]:
    eligible = [
        item
        for item in candidates
        if item["val_metrics"]["precision"] >= precision_floor and item["val_metrics"]["predicted_positive"] > 0
    ]
    if eligible:
        selected = max(
            eligible,
            key=lambda item: (
                item["val_metrics"]["recall"],
                item["val_metrics"]["precision"],
                item["val_metrics"]["accuracy"],
                -item["prompt_length"],
            ),
        )
        strategy = {
            "mode": "recall_with_precision_floor",
            "precision_floor": precision_floor,
            "eligible_variants": [item["variant"] for item in eligible],
            "fallback_used": False,
        }
        return selected, strategy

    selected = max(
        candidates,
        key=lambda item: (
            item["val_metrics"]["precision"],
            item["val_metrics"]["recall"],
            item["val_metrics"]["accuracy"],
            -item["prompt_length"],
        ),
    )
    strategy = {
        "mode": "precision_fallback",
        "precision_floor": precision_floor,
        "eligible_variants": [],
        "fallback_used": True,
    }
    return selected, strategy


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
            "train_metrics": strict_metrics(train_summary),
            "val_metrics": strict_metrics(val_summary),
        }
        candidates.append(candidate)

    selected, selection_strategy = pick_candidate(candidates, precision_floor=args.strict_precision_floor)

    selected_prompt_path = Path(selected["prompt_path"])
    test_summary = run_selected_test(
        input_runs_dir=args.input_root / "test" / "runs",
        output_dir=args.selected_test_dir,
        prompt_path=selected_prompt_path,
        model=args.model,
        overwrite=args.overwrite,
    )

    frozen_dir = args.results_dir / "selected"
    frozen_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(selected_prompt_path, frozen_dir / "system_prompt.md")

    baseline = load_baseline(args.baseline_summary)
    test_metrics = strict_metrics(test_summary)
    comparison = {
        "train": {
            "baseline": baseline["train"],
            "candidate": selected["train"],
            "baseline_metrics": strict_metrics(baseline["train"]),
            "candidate_metrics": selected["train_metrics"],
        },
        "val": {
            "baseline": baseline["val"],
            "candidate": selected["val"],
            "baseline_metrics": strict_metrics(baseline["val"]),
            "candidate_metrics": selected["val_metrics"],
            "strict_delta": selected["val"]["strict"]["accuracy"] - baseline["val"]["strict"]["accuracy"],
            "lenient_delta": selected["val"]["lenient"]["accuracy"] - baseline["val"]["lenient"]["accuracy"],
        },
        "test": {
            "baseline": baseline["test"],
            "candidate": test_summary,
            "baseline_metrics": strict_metrics(baseline["test"]),
            "candidate_metrics": test_metrics,
            "strict_delta": test_summary["strict"]["accuracy"] - baseline["test"]["strict"]["accuracy"],
            "lenient_delta": test_summary["lenient"]["accuracy"] - baseline["test"]["lenient"]["accuracy"],
        },
    }

    artifacts = {
        "candidates": candidates,
        "selection_strategy": selection_strategy,
        "selected_variant": selected["variant"],
        "selected_prompt_path": str(selected_prompt_path),
        "test": test_summary,
        "test_metrics": test_metrics,
        "comparison": comparison,
    }
    (args.results_dir / "selection_summary.json").write_text(
        json.dumps(artifacts, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(artifacts, indent=2))


if __name__ == "__main__":
    main()
