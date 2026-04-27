#!/usr/bin/env python3
"""End-to-end train/val/test pipeline for the ICLR 2026 desk-reject task."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from backtest_iclr_desk_reject import load_manifest, run_one, summarize
from iclr_desk_reject_model import evaluate_rows, fit_bernoulli_nb, load_rows, summarize_results
from scrape_iclr_2026_batch import fetch_candidate_notes, write_dataset


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--splits-dir",
        type=Path,
        default=Path("experimental/data/iclr_2026_desk_reject_splits"),
    )
    parser.add_argument(
        "--results-dir",
        type=Path,
        default=Path("experimental/results/iclr_2026_desk_reject_pipeline"),
    )
    parser.add_argument(
        "--agent-prompt",
        type=Path,
        default=Path("agent_configs/iclr-desk-reject/system_prompt.md"),
    )
    parser.add_argument("--model", default="gpt-5.4")
    parser.add_argument("--train-total", type=int, default=40)
    parser.add_argument("--val-total", type=int, default=20)
    parser.add_argument("--test-total", type=int, default=20)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--build-splits", action="store_true")
    parser.add_argument("--clean-results", action="store_true")
    parser.add_argument("--skip-agent", action="store_true")
    parser.add_argument(
        "--prompt-runs-root",
        type=Path,
        default=None,
        help="Optional existing prompt-runs root with train/ val/ test subdirs when --skip-agent is used.",
    )
    parser.add_argument("--max-pages", type=int, default=60)
    parser.add_argument("--sleep-seconds", type=float, default=0.25)
    parser.add_argument(
        "--min-df-candidates",
        default="1,2,3,5",
        help="Comma-separated candidate min_df values for calibration model selection.",
    )
    parser.add_argument("--overwrite-agent-runs", action="store_true")
    return parser.parse_args()


def _require_even(name: str, total: int) -> int:
    if total <= 0 or total % 2 != 0:
        raise SystemExit(f"{name} must be a positive even integer")
    return total // 2


def build_splits(args: argparse.Namespace) -> None:
    per_class = {
        "train": _require_even("train-total", args.train_total),
        "val": _require_even("val-total", args.val_total),
        "test": _require_even("test-total", args.test_total),
    }
    total_needed_per_class = sum(per_class.values())
    candidates = fetch_candidate_notes(
        per_class=max(total_needed_per_class * 2, total_needed_per_class),
        max_pages=args.max_pages,
        sleep_seconds=args.sleep_seconds,
    )

    import random

    rng = random.Random(args.seed)
    for label in candidates:
        rng.shuffle(candidates[label])
        if len(candidates[label]) < total_needed_per_class:
            raise SystemExit(
                f"Only found {len(candidates[label])} candidates for {label}, need {total_needed_per_class}"
            )

    offsets = {"desk_rejected": 0, "not_desk_rejected": 0}
    args.splits_dir.mkdir(parents=True, exist_ok=True)
    seen_ids = set()
    for split_name in ["train", "val", "test"]:
        selected = {}
        for label in ["desk_rejected", "not_desk_rejected"]:
            count = per_class[split_name]
            notes = candidates[label][offsets[label] : offsets[label] + count]
            if len(notes) < count:
                raise SystemExit(f"Not enough candidates to create {split_name}")
            selected[label] = notes
            offsets[label] += count

        ids = {note["id"] for notes in selected.values() for note in notes}
        overlap = ids & seen_ids
        if overlap:
            raise RuntimeError(f"Split overlap detected in {split_name}: {sorted(overlap)}")
        seen_ids |= ids
        write_dataset(args.splits_dir / split_name, selected, seed=args.seed)

    split_plan = {
        "seed": args.seed,
        "totals": {
            "train": args.train_total,
            "val": args.val_total,
            "test": args.test_total,
        },
    }
    (args.splits_dir / "split_plan.json").write_text(json.dumps(split_plan, indent=2) + "\n", encoding="utf-8")


def run_agent_split(
    dataset_dir: Path,
    output_dir: Path,
    agent_prompt_path: Path,
    model: str,
    overwrite: bool,
) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest(dataset_dir / "manifest.jsonl")
    results = []
    for row in manifest:
        print(f"[agent] {dataset_dir.name}: {row['forum_id']} ({row['label']})", flush=True)
        results.append(
            run_one(
                row=row,
                dataset_dir=dataset_dir,
                agent_prompt_path=agent_prompt_path,
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


def main() -> None:
    args = parse_args()
    if args.clean_results and args.results_dir.exists():
        shutil.rmtree(args.results_dir)
    args.results_dir.mkdir(parents=True, exist_ok=True)

    if args.build_splits:
        build_splits(args)

    prompt_root = args.prompt_runs_root or (args.results_dir / "prompt_runs")
    prompt_summaries = {}
    if args.skip_agent:
        for split_name in ["train", "val", "test"]:
            summary_path = prompt_root / split_name / "summary.json"
            if summary_path.exists():
                prompt_summaries[split_name] = json.loads(summary_path.read_text(encoding="utf-8"))
            else:
                prompt_summaries[split_name] = None
    else:
        for split_name in ["train", "val", "test"]:
            prompt_summaries[split_name] = run_agent_split(
                dataset_dir=args.splits_dir / split_name,
                output_dir=prompt_root / split_name,
                agent_prompt_path=args.agent_prompt,
                model=args.model,
                overwrite=args.overwrite_agent_runs,
            )

    train_rows = load_rows(prompt_root / "train" / "runs")
    val_rows = load_rows(prompt_root / "val" / "runs")
    min_df_candidates = [int(part) for part in args.min_df_candidates.split(",") if part.strip()]

    val_search = []
    for min_df in min_df_candidates:
        model = fit_bernoulli_nb(train_rows, min_df=min_df)
        val_results = evaluate_rows(val_rows, model)
        val_summary = summarize_results(
            val_results,
            note="Validation summary for model selection; model fit on train only.",
        )
        val_search.append({"min_df": min_df, "summary": val_summary})

    best = max(val_search, key=lambda item: item["summary"]["accuracy"])
    final_train_rows = train_rows + val_rows
    final_model = fit_bernoulli_nb(final_train_rows, min_df=best["min_df"])

    calibrated_root = args.results_dir / "calibrated"
    calibrated_root.mkdir(parents=True, exist_ok=True)
    (calibrated_root / "model.json").write_text(json.dumps(final_model, indent=2) + "\n", encoding="utf-8")

    calibrated_summaries = {}
    for split_name, note in [
        ("train", "Calibrated train summary; model fit on train+val for final evaluation."),
        ("val", "Calibrated validation summary; model fit on train+val for final evaluation."),
        ("test", "Calibrated test summary; model fit on train+val and applied out of sample."),
    ]:
        rows = load_rows(prompt_root / split_name / "runs")
        results = evaluate_rows(rows, final_model)
        summary = summarize_results(results, note=note)
        split_dir = calibrated_root / split_name
        split_dir.mkdir(parents=True, exist_ok=True)
        (split_dir / "results.jsonl").write_text(
            "\n".join(json.dumps(item, ensure_ascii=True) for item in results) + "\n",
            encoding="utf-8",
        )
        (split_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        calibrated_summaries[split_name] = summary

    pipeline_summary = {
        "prompt_only": prompt_summaries,
        "validation_search": val_search,
        "selected_min_df": best["min_df"],
        "calibrated": calibrated_summaries,
    }
    (args.results_dir / "pipeline_summary.json").write_text(
        json.dumps(pipeline_summary, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(pipeline_summary, indent=2))


if __name__ == "__main__":
    main()
