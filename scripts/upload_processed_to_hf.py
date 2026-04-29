from __future__ import annotations

import argparse
import json
import os
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
        for paper_dir in sorted(p.name for p in subset_root.iterdir() if p.is_dir()):
            if allowed is not None and paper_dir not in allowed:
                continue
            local_path = subset_root / paper_dir
            upload_path = f"{subset}/{paper_dir}"
            api.upload_folder(
                folder_path=str(local_path),
                path_in_repo=upload_path,
                repo_id=args.hf_repo,
                repo_type="dataset",
                token=token,
                allow_patterns="*" if args.overwrite else None,
                ignore_patterns=["*.log"],
            )
            print(f"uploaded {upload_path}")


if __name__ == "__main__":
    main()
