from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Upload parsed Paper2Markdown-V3 data to HuggingFace.")
    parser.add_argument("--dataset-root", type=Path, default=Path("data/processed_papers"))
    parser.add_argument("--subset", type=str, choices=["icml26", "iclr26"], default=None)
    parser.add_argument("--source-manifest", type=Path, default=None)
    parser.add_argument("--hf-repo", type=str, required=True, help="repo id like jzshared/agent_paper_review")
    parser.add_argument("--token", type=str, default=None)
    parser.add_argument("--private", action="store_true", help="Create private repo if missing.")
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing remote files in target folder.",
    )
    return parser.parse_args()


def _load_manifest(path: Path) -> set[str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {row["paper_id"] for row in payload if isinstance(row, dict) and row.get("paper_id")}


def _copy_subset_metadata(subset_root: Path, destination: Path) -> None:
    for path in subset_root.iterdir():
        if path.is_file() and not path.name.endswith(".log"):
            shutil.copy2(path, destination / path.name)


def _stage_filtered_subset(subset_root: Path, allowed: set[str], staging_root: Path) -> tuple[Path, int]:
    staged_subset = staging_root / subset_root.name
    staged_subset.mkdir(parents=True, exist_ok=True)
    _copy_subset_metadata(subset_root, staged_subset)
    copied = 0
    for paper_dir in sorted(path for path in subset_root.iterdir() if path.is_dir()):
        if paper_dir.name not in allowed:
            continue
        shutil.copytree(paper_dir, staged_subset / paper_dir.name)
        copied += 1
    return staged_subset, copied


def _paper_dir_count(subset_root: Path, allowed: set[str] | None) -> int:
    return sum(
        1
        for paper_dir in subset_root.iterdir()
        if paper_dir.is_dir() and (allowed is None or paper_dir.name in allowed)
    )


def _upload_subset(api, *, subset_root: Path, subset: str, args: argparse.Namespace, token: str, allowed: set[str] | None) -> None:
    if allowed is None:
        upload_root = subset_root
        count = _paper_dir_count(subset_root, allowed)
        api.upload_folder(
            folder_path=str(upload_root),
            path_in_repo=subset,
            repo_id=args.hf_repo,
            repo_type="dataset",
            token=token,
            allow_patterns="*" if args.overwrite else None,
            ignore_patterns=["*.log"],
        )
    else:
        with tempfile.TemporaryDirectory(prefix=f"{subset}_hf_upload_") as tmp:
            upload_root, count = _stage_filtered_subset(subset_root, allowed, Path(tmp))
            api.upload_folder(
                folder_path=str(upload_root),
                path_in_repo=subset,
                repo_id=args.hf_repo,
                repo_type="dataset",
                token=token,
                allow_patterns="*" if args.overwrite else None,
                ignore_patterns=["*.log"],
            )
    print(f"uploaded subset {subset} papers={count}")


def main() -> None:
    args = parse_args()
    try:
        from huggingface_hub import HfApi
    except Exception as exc:
        raise RuntimeError("huggingface_hub is required for upload: pip install huggingface_hub") from exc

    token = args.token or os.getenv("HUGGINGFACE_HUB_TOKEN")
    if not token:
        raise RuntimeError("Hugging face token missing, set --token or HUGGINGFACE_HUB_TOKEN.")

    api = HfApi()
    api.create_repo(repo_id=args.hf_repo, repo_type="dataset", private=args.private, exist_ok=True, token=token)

    allowed = _load_manifest(args.source_manifest) if args.source_manifest else None
    subsets = [args.subset] if args.subset else ["icml26", "iclr26"]

    for subset in subsets:
        subset_root = args.dataset_root / subset
        if not subset_root.exists():
            print(f"skip subset {subset}: missing path {subset_root}")
            continue
        _upload_subset(api, subset_root=subset_root, subset=subset, args=args, token=token, allowed=allowed)


if __name__ == "__main__":
    main()
