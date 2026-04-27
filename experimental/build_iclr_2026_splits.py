#!/usr/bin/env python3
"""Create disjoint train/val/test ICLR 2026 desk-reject splits."""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

from scrape_iclr_2026_batch import fetch_candidate_notes, write_dataset


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--train-total", type=int, default=40)
    parser.add_argument("--val-total", type=int, default=20)
    parser.add_argument("--test-total", type=int, default=20)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument(
        "--outdir",
        type=Path,
        default=Path("experimental/data/iclr_2026_desk_reject_splits"),
    )
    parser.add_argument("--max-pages", type=int, default=60)
    parser.add_argument("--sleep-seconds", type=float, default=0.25)
    return parser.parse_args()


def _require_even(name: str, total: int) -> int:
    if total <= 0 or total % 2 != 0:
        raise SystemExit(f"{name} must be a positive even integer")
    return total // 2


def _allocate_split(
    notes: list[dict],
    offset: int,
    count: int,
) -> tuple[list[dict], int]:
    selected = notes[offset : offset + count]
    if len(selected) < count:
        raise SystemExit(f"Not enough disjoint candidates to allocate {count} papers")
    return selected, offset + count


def main() -> None:
    args = parse_args()
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

    rng = random.Random(args.seed)
    for label in candidates:
        rng.shuffle(candidates[label])
        if len(candidates[label]) < total_needed_per_class:
            raise SystemExit(
                f"Only found {len(candidates[label])} candidates for {label}, need {total_needed_per_class}"
            )

    offsets = {"desk_rejected": 0, "not_desk_rejected": 0}
    split_rows = {}
    for split_name in ["train", "val", "test"]:
        split_rows[split_name] = {}
        for label in ["desk_rejected", "not_desk_rejected"]:
            selected, new_offset = _allocate_split(
                candidates[label],
                offsets[label],
                per_class[split_name],
            )
            split_rows[split_name][label] = selected
            offsets[label] = new_offset

    args.outdir.mkdir(parents=True, exist_ok=True)
    metadata = {
        "seed": args.seed,
        "totals": {
            "train": args.train_total,
            "val": args.val_total,
            "test": args.test_total,
        },
    }
    (args.outdir / "split_plan.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")

    seen_ids = set()
    for split_name, by_label in split_rows.items():
        split_dir = args.outdir / split_name
        ids = {note["id"] for notes in by_label.values() for note in notes}
        overlap = seen_ids & ids
        if overlap:
            raise RuntimeError(f"Split overlap detected for {split_name}: {sorted(overlap)}")
        seen_ids |= ids
        write_dataset(split_dir, by_label, seed=args.seed)

    print(f"Wrote disjoint splits to {args.outdir}")


if __name__ == "__main__":
    main()
