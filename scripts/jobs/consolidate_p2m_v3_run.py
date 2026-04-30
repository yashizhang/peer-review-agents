from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Consolidate one run into canonical processed_papers/{subset}/{paper_id} layout.")
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--run-name", type=str, required=True)
    parser.add_argument("--subset", type=str, choices=["icml26", "iclr26"], required=True)
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing paper folders.")
    parser.add_argument("--run-manifest", type=Path, default=None)
    parser.add_argument("--processed-subdir", type=str, default="processed_v3")
    parser.add_argument("--source-manifest", type=Path, default=None)
    return parser.parse_args()


def _load_rows(path: Path | None, fallback: Path) -> list[dict]:
    if path is None:
        if not fallback.exists():
            return []
        path = fallback
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        return [payload]
    return []


def main() -> None:
    args = parse_args()
    run_root = args.run_root / args.run_name
    source_root = run_root / args.processed_subdir
    if not source_root.exists():
        raise FileNotFoundError(f"processed folder missing: {source_root}")

    manifest_rows = _load_rows(
        args.run_manifest,
        run_root / "input_manifest.json",
    )

    if args.source_manifest:
        rows = _load_rows(args.source_manifest, Path("/dev/null"))
        manifest_rows.extend(rows)

    source_map = {}
    for row in manifest_rows:
        if isinstance(row, dict):
            paper_id = row.get("paper_id")
            if paper_id:
                source_map[paper_id] = row

    target_root = args.run_root / args.subset
    target_root.mkdir(parents=True, exist_ok=True)

    copied = 0
    skipped = 0
    for paper_dir in sorted(source_root.iterdir()):
        if not paper_dir.is_dir():
            continue
        paper_id = paper_dir.name
        target = target_root / paper_id
        if target.exists():
            if args.overwrite:
                shutil.rmtree(target)
            else:
                skipped += 1
                continue
        shutil.copytree(paper_dir, target)
        copied += 1

        report_path = paper_dir / "sanitization_report.json"
        if report_path.exists():
            payload = json.loads(report_path.read_text(encoding="utf-8"))
        else:
            payload = {}
        payload["source"] = source_map.get(paper_id, {}).get("source", args.subset)
        payload["run_name"] = args.run_name
        payload["paper_id"] = paper_id
        (target / "dataset_meta.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(json.dumps({"copied": copied, "skipped": skipped, "target": str(target_root)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
