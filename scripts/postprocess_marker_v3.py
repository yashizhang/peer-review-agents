from __future__ import annotations

import argparse
import json
import shutil
from collections import Counter
from pathlib import Path
from typing import Any

from koala_strategy.paper.marker_v2 import (
    audit_model_facing_texts,
    build_marker_v2_chunks,
    filter_marker_assets,
    render_model_text_v3,
    split_chunks_by_view,
)
from koala_strategy.paper.text_sanitizer import sanitize_model_text


DEFAULT_LEAK_TERMS = [
    "Anonymous Authors",
    "ACKNOWLEDGMENT",
    "OpenReview",
    "Accepted",
    "Reject",
    "Meta-review",
    "Official Review",
]


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def _paths(input_root: Path, paper_id: str) -> dict[str, Path]:
    paper_dir = input_root / paper_id
    return {
        "parse_report": paper_dir / "parse_report.json",
        "blocks": paper_dir / "marker_chunks" / paper_id / f"{paper_id}.json",
        "markdown": paper_dir / "marker_markdown" / paper_id / f"{paper_id}.md",
        "markdown_dir": paper_dir / "marker_markdown" / paper_id,
        "marker_meta": paper_dir / "marker_markdown" / paper_id / f"{paper_id}_meta.json",
    }


def postprocess_paper(
    input_root: Path,
    output_root: Path,
    paper_id: str,
    leak_terms: list[str],
    copy_assets_all: bool = False,
) -> dict[str, Any]:
    paths = _paths(input_root, paper_id)
    missing = [name for name, path in paths.items() if name != "marker_meta" and not path.exists()]
    if missing:
        return {"paper_id": paper_id, "ok": False, "error": f"missing inputs: {', '.join(missing)}"}

    parse_report = _read_json(paths["parse_report"])
    page_count = int(parse_report["page_count"])
    blocks_payload = _read_json(paths["blocks"])
    raw_markdown = paths["markdown"].read_text(encoding="utf-8")
    chunks = build_marker_v2_chunks(blocks_payload, paper_id=paper_id, page_count=page_count)
    views = split_chunks_by_view(chunks)

    out_dir = output_root / paper_id
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    assets_dir = out_dir / "assets"
    assets_dir.mkdir()
    assets_all_dir = out_dir / "assets_all"
    if copy_assets_all:
        assets_all_dir.mkdir()

    shutil.copy2(paths["blocks"], out_dir / "paper.blocks.json")
    shutil.copy2(paths["markdown"], out_dir / "paper.md")
    if paths["marker_meta"].exists():
        shutil.copy2(paths["marker_meta"], out_dir / "marker_meta.json")

    sanitized = sanitize_model_text(raw_markdown)
    (out_dir / "sanitized_v3.txt").write_text(sanitized + "\n", encoding="utf-8")
    _write_jsonl(out_dir / "chunks_v3_anonymized.jsonl", chunks)
    _write_jsonl(out_dir / "main_body_chunks.jsonl", views["main_body_chunks"])
    _write_jsonl(out_dir / "appendix_chunks.jsonl", views["appendix_chunks"])
    _write_jsonl(out_dir / "reference_chunks.jsonl", views["reference_chunks"])

    model_text = render_model_text_v3(views["main_body_chunks"])
    appendix_text = render_model_text_v3(views["appendix_chunks"])
    reference_text = render_model_text_v3(views["reference_chunks"])
    (out_dir / "model_text_v3.txt").write_text(model_text + "\n", encoding="utf-8")
    (out_dir / "appendix_text_v3.txt").write_text(appendix_text + "\n", encoding="utf-8")
    (out_dir / "reference_text_v3.txt").write_text(reference_text + "\n", encoding="utf-8")

    asset_manifest = []
    for entry in filter_marker_assets(paths["markdown_dir"].glob("*.jpeg")):
        source = Path(entry["path"])
        out_entry = dict(entry)
        out_entry["path"] = str(source)
        out_entry["model_path"] = None
        if copy_assets_all:
            shutil.copy2(source, assets_all_dir / source.name)
            out_entry["audit_path"] = f"assets_all/{source.name}"
        if entry["keep"]:
            shutil.copy2(source, assets_dir / source.name)
            out_entry["model_path"] = f"assets/{source.name}"
        asset_manifest.append(out_entry)
    _write_json(out_dir / "assets.json", asset_manifest)

    chunks_jsonl = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in chunks)
    audit = audit_model_facing_texts(
        {
            "model_text_v3.txt": model_text,
            "chunks_v3_anonymized.jsonl": chunks_jsonl,
        },
        leak_terms=leak_terms,
    )
    page_values = [int(chunk["page_start"]) for chunk in chunks]
    block_counts = Counter(str(block.get("block_type") or "") for block in blocks_payload.get("blocks", []))
    asset_reasons = Counter(entry["reject_reason"] or "kept" for entry in asset_manifest)
    report = {
        "paper_id": paper_id,
        "pipeline": "Paper2Markdown-V3",
        "ok": audit["ok"],
        "page_count": page_count,
        "chunk_count": len(chunks),
        "main_body_chunk_count": len(views["main_body_chunks"]),
        "appendix_chunk_count": len(views["appendix_chunks"]),
        "reference_chunk_count": len(views["reference_chunks"]),
        "model_text_chars": len(model_text),
        "raw_markdown_chars": len(raw_markdown),
        "sanitized_chars": len(sanitized),
        "page_provenance": {
            "min_page": min(page_values) if page_values else None,
            "max_page": max(page_values) if page_values else None,
            "invalid_count": sum(1 for value in page_values if value < 1 or value > page_count),
        },
        "marker_block_type_counts": dict(sorted(block_counts.items())),
        "asset_count_raw": len(asset_manifest),
        "asset_count_model_kept": sum(1 for entry in asset_manifest if entry["keep"]),
        "asset_count_rejected": sum(1 for entry in asset_manifest if not entry["keep"]),
        "asset_reject_reasons": dict(sorted(asset_reasons.items())),
        "artifact_leak_audit": audit,
        "default_model_input": "model_text_v3.txt",
        "appendix_input": "appendix_text_v3.txt",
        "reference_input": "reference_text_v3.txt",
    }
    _write_json(out_dir / "parse_report.json", {**parse_report, "paper2markdown_v3": report})
    _write_json(out_dir / "sanitization_report.json", report)
    return {"paper_id": paper_id, "ok": audit["ok"], "output_dir": str(out_dir), **report}


def main() -> None:
    parser = argparse.ArgumentParser(description="Build Paper2Markdown-V3 model-facing artifacts from Marker outputs.")
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--paper-id", action="append", default=[])
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--summary-path", type=Path, default=None)
    parser.add_argument("--copy-assets-all", action="store_true")
    parser.add_argument("--leak-term", action="append", default=[])
    args = parser.parse_args()

    paper_ids = args.paper_id or sorted(path.name for path in args.input_root.iterdir() if path.is_dir())
    if args.limit is not None:
        paper_ids = paper_ids[: args.limit]
    leak_terms = DEFAULT_LEAK_TERMS + list(args.leak_term)
    results = [postprocess_paper(args.input_root, args.output_root, paper_id, leak_terms, args.copy_assets_all) for paper_id in paper_ids]
    summary_path = args.summary_path or args.output_root / "summary.json"
    args.output_root.mkdir(parents=True, exist_ok=True)
    _write_json(summary_path, results)
    print(json.dumps({"summary_path": str(summary_path), "papers": len(results), "ok": sum(1 for row in results if row.get("ok"))}, indent=2))


if __name__ == "__main__":
    main()
