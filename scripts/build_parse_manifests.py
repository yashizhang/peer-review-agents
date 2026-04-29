from __future__ import annotations

import argparse
import json
from pathlib import Path

from koala_strategy.data.parse_manifests import (
    build_icml_manifest,
    build_iclr_manifest,
    dedupe_manifest_rows,
    write_manifest,
)


def _write(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_manifest(rows, path)
    payload = {"count": len(rows), "path": str(path)}
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def build_icml(args: argparse.Namespace) -> None:
    rows = build_icml_manifest(
        csv_path=args.csv,
        existing_manifest=args.existing,
    )
    rows = dedupe_manifest_rows(rows)
    _write(args.output, rows)


def build_iclr(args: argparse.Namespace) -> None:
    rows = build_iclr_manifest(
        train_jsonl=args.train,
        test_jsonl=args.test,
        prefer_test_first=args.prefer_test_first,
    )
    rows = dedupe_manifest_rows(rows)
    _write(args.output, rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build paper parsing manifests for V3 queue jobs.")
    subparsers = parser.add_subparsers(dest="mode", required=True)

    icml = subparsers.add_parser("icml")
    icml.add_argument("--csv", type=Path, required=True)
    icml.add_argument("--existing", type=Path, default=None)
    icml.add_argument("--output", type=Path, required=True)
    icml.set_defaults(func=build_icml)

    iclr = subparsers.add_parser("iclr")
    iclr.add_argument("--train", type=Path, required=True)
    iclr.add_argument("--test", type=Path, default=None)
    iclr.add_argument(
        "--prefer-test-first",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Prefer global_test_public rows when the same paper appears in both sets.",
    )
    iclr.add_argument("--output", type=Path, required=True)
    iclr.set_defaults(func=build_iclr)

    return parser.parse_args()


def main() -> None:
    args = parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
