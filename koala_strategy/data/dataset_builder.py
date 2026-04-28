from __future__ import annotations

from pathlib import Path
from typing import Any

from koala_strategy.config import load_config, resolve_path
from koala_strategy.data.iclr_loader import load_iclr_examples
from koala_strategy.utils import dump_json, ensure_dir, write_jsonl


def build_iclr_dataset(config: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = config or load_config()
    examples = load_iclr_examples(config=cfg)
    out_dir = ensure_dir(resolve_path(cfg, "iclr_processed_dir"))
    rows = [ex.model_dump(mode="json") for ex in examples]
    write_jsonl(out_dir / "examples.jsonl", rows)
    summary = {
        "num_examples": len(rows),
        "num_accept": sum(1 for ex in examples if ex.decision == "accept"),
        "num_reject": sum(1 for ex in examples if ex.decision == "reject"),
        "output": str(out_dir / "examples.jsonl"),
    }
    dump_json(out_dir / "summary.json", summary)
    return summary


def processed_examples_path(config: dict[str, Any] | None = None) -> Path:
    cfg = config or load_config()
    return resolve_path(cfg, "iclr_processed_dir") / "examples.jsonl"

