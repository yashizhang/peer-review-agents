from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from koala_strategy.data.parse_manifests import _safe_url_from_record


MarkerOutput = tuple[str, str, str]


def _safe_path(path: Path) -> str:
    return str(path.resolve())


def _run_command(command: list[str], *, timeout: int | None = None) -> tuple[int, str]:
    try:
        completed = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout,
        )
        return completed.returncode, completed.stdout or ""
    except subprocess.TimeoutExpired as exc:
        return 1, f"timeout after {timeout}s: {exc}"
    except OSError as exc:
        return 1, f"command start failed: {exc}"


def _download_pdf(url: str, destination: Path, *, timeout_seconds: int = 120) -> tuple[bool, int, str | None]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    hasher = hashlib.sha256()
    bytes_count = 0
    try:
        if destination.exists():
            destination.unlink()
    except OSError:
        pass

    status_code = -1
    curl_cmd = shutil.which("curl")
    if curl_cmd:
        code, log = _run_command(
            [
                curl_cmd,
                "-L",
                "--fail",
                "--show-error",
                "--max-time",
                str(timeout_seconds),
                "--location-trusted",
                "--output",
                str(destination),
                url,
            ],
            timeout=timeout_seconds + 20,
        )
        status_code = 0 if code == 0 else 1
        if code != 0:
            if destination.exists():
                destination.unlink()
            return False, status_code, None
    else:
        wget_cmd = shutil.which("wget")
        if not wget_cmd:
            return False, status_code, None
        code, log = _run_command(
            [wget_cmd, "--timeout", str(timeout_seconds), "-O", str(destination), url],
            timeout=timeout_seconds + 20,
        )
        status_code = 0 if code == 0 else 1
        if code != 0:
            if destination.exists():
                destination.unlink()
            return False, status_code, None

    if not destination.exists():
        return False, status_code, None

    with destination.open("rb") as handle:
        for chunk in iter(lambda: handle.read(2**20), b""):
            if not chunk:
                continue
            hasher.update(chunk)
            bytes_count += len(chunk)
    return True, bytes_count, hasher.hexdigest()


def _page_count_from_pdf(pdf_path: Path) -> int:
    try:
        import fitz
    except Exception as exc:
        raise RuntimeError(f"PyMuPDF unavailable: {exc}") from exc
    with fitz.open(pdf_path) as doc:
        return int(doc.page_count)


def _page_log_path(paper_root: Path, stem: str) -> Path:
    return paper_root / f"marker_{stem}.log"


def run_marker_for_paper(
    marker_binary: str,
    pdf_path: Path,
    paper_root: Path,
    *,
    timeout_seconds: int,
) -> tuple[bool, dict[str, str]]:
    times: dict[str, str] = {}
    outputs: list[MarkerOutput] = [
        ("marker_markdown", "markdown", "markdown"),
        ("marker_chunks", "chunks", "chunks"),
    ]
    for out_dir_name, output_fmt, timer_key in outputs:
        out_dir = paper_root / out_dir_name
        command = [
            marker_binary,
            _safe_path(pdf_path),
            "--output_dir",
            _safe_path(out_dir),
            "--output_format",
            output_fmt,
            "--paginate_output",
            "--disable_tqdm",
            "--disable_multiprocessing",
        ]
        start = time.time()
        code, log = _run_command(command, timeout=timeout_seconds)
        times[f"{timer_key}_sec"] = f"{time.time() - start:.2f}"
        page_log = _page_log_path(paper_root, timer_key)
        page_log.parent.mkdir(parents=True, exist_ok=True)
        page_log.write_text(log, encoding="utf-8")
        if code != 0:
            return False, times
    return True, times


def run_postprocess(paper_id: str, marker_root: Path, output_root: Path) -> tuple[bool, str]:
    python_bin = os.environ.get("PYTHON_BIN", "python")
    command = [
        python_bin,
        str(Path(__file__).resolve().parents[1] / "postprocess_marker_v3.py"),
        "--input-root",
        _safe_path(marker_root),
        "--output-root",
        _safe_path(output_root),
        "--paper-id",
        paper_id,
    ]
    code, log = _run_command(command, timeout=1200)
    (output_root / f"{paper_id}_postprocess.log").write_text(log, encoding="utf-8")
    return code == 0, log


def _ensure_parse_report(
    paper_root: Path,
    *,
    paper_id: str,
    pdf_path: Path,
    download_url: str,
    parse_ok: bool,
    page_count: int,
    sha256: str,
    bytes_count: int,
    elapsed_seconds: float,
) -> None:
    parse_report = {
        "paper_id": paper_id,
        "pipeline": "marker_non_llm_v3",
        "parser": "marker_single",
        "formats": ["markdown", "chunks"],
        "llm_enabled": False,
        "pdf_path": _safe_path(pdf_path),
        "pdf_sha256": sha256,
        "bytes": bytes_count,
        "source": download_url,
        "page_count": page_count,
        "ok": parse_ok,
        "elapsed_seconds": elapsed_seconds,
    }
    (paper_root / "parse_report.json").write_text(json.dumps(parse_report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _normalize_url(url: str | None) -> str | None:
    return _safe_url_from_record({"url": url}) if url else None


def _load_manifest(path: Path) -> list[dict[str, Any]]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise RuntimeError(f"Manifest must be a list: {path}")
    return [dict(row) for row in rows if isinstance(row, dict)]


def _select_shard(rows: list[dict[str, Any]], shard_index: int, shard_count: int) -> list[dict[str, Any]]:
    return [row for index, row in enumerate(rows) if index % shard_count == shard_index]


def run_worker(
    *,
    run_root: Path,
    run_name: str,
    manifest_path: Path,
    shard_index: int,
    shard_count: int,
    marker_binary: str,
    marker_timeout_seconds: int,
    worker_id: str,
    max_retries: int,
) -> list[dict[str, Any]]:
    rows = _load_manifest(manifest_path)
    papers = _select_shard(rows, shard_index=shard_index, shard_count=shard_count)
    if not papers:
        return []

    run_dir = run_root / run_name
    raw_root = run_dir / "raw"
    marker_root = run_dir / "marker_raw"
    output_root = run_dir / "processed_v3"
    run_dir.mkdir(parents=True, exist_ok=True)
    raw_root.mkdir(parents=True, exist_ok=True)
    marker_root.mkdir(parents=True, exist_ok=True)
    output_root.mkdir(parents=True, exist_ok=True)

    rows_out: list[dict[str, Any]] = []
    for row in papers:
        paper_id = (str(row.get("paper_id") or "")).strip()
        if not paper_id:
            continue

        paper_row: dict[str, Any] = {
            "paper_id": paper_id,
            "run_name": run_name,
            "shard_index": shard_index,
            "shard_count": shard_count,
            "worker_id": worker_id,
            "source": row.get("source"),
            "title": row.get("title"),
            "abstract": row.get("abstract"),
        }

        processed_dir = output_root / paper_id
        if (processed_dir / "sanitization_report.json").exists():
            paper_row["ok"] = True
            paper_row["skipped"] = True
            rows_out.append(paper_row)
            continue

        raw_pdf = raw_root / f"{paper_id}.pdf"
        raw_url = _normalize_url(row.get("download_url"))
        if not raw_url:
            raw_url = f"https://koala.science/storage/pdfs/{paper_id}.pdf"
        paper_row["url"] = raw_url

        digest = row.get("pdf_sha256")
        raw_bytes = raw_pdf.stat().st_size if raw_pdf.exists() else 0
        for _ in range(max_retries + 1):
            if raw_pdf.exists() and raw_bytes > 64:
                break
            try:
                ok, bytes_count, got_digest = _download_pdf(raw_url, raw_pdf, timeout_seconds=marker_timeout_seconds)
            except Exception as exc:
                paper_row["error"] = f"download_exception:{exc}"
                ok = False
                bytes_count = 0
                got_digest = None
            if ok:
                digest = got_digest
                raw_bytes = bytes_count
                break
            paper_row["error"] = f"download_failed:{bytes_count}"
            raw_bytes = 0
            if raw_pdf.exists():
                raw_pdf.unlink()
        else:
            paper_row["ok"] = False
            rows_out.append(paper_row)
            continue

        if not raw_pdf.exists() or raw_pdf.stat().st_size < 64:
            paper_row.update(ok=False, error="download_failed_small_file")
            rows_out.append(paper_row)
            continue

        digest = digest or hashlib.sha256(raw_pdf.read_bytes()).hexdigest()
        paper_root = marker_root / paper_id
        marker_ok, marker_times = run_marker_for_paper(
            marker_binary,
            raw_pdf,
            paper_root,
            timeout_seconds=marker_timeout_seconds,
        )
        paper_row.update(marker_times)
        if not marker_ok:
            paper_row.update(ok=False, error="marker_failed")
            rows_out.append(paper_row)
            continue

        parse_report_path = paper_root / "marker_markdown" / paper_id / "parse_report.json"
        if parse_report_path.exists():
            parse_report = json.loads(parse_report_path.read_text(encoding="utf-8"))
        else:
            try:
                page_count = _page_count_from_pdf(raw_pdf)
            except Exception as exc:
                paper_row.update(ok=False, error=f"page_count_failed:{exc}")
                rows_out.append(paper_row)
                continue
            _ensure_parse_report(
                paper_root / "marker_markdown" / paper_id,
                paper_id=paper_id,
                pdf_path=raw_pdf,
                download_url=raw_url,
                parse_ok=marker_ok,
                page_count=page_count,
                sha256=digest,
                bytes_count=raw_bytes,
                elapsed_seconds=sum(float(v) for v in marker_times.values() if v),
            )
            parse_report = json.loads(parse_report_path.read_text(encoding="utf-8"))

        ok, postprocess_log = run_postprocess(paper_id, marker_root, output_root)
        paper_row["postprocess_ok"] = ok
        paper_row["postprocess_log_head"] = (postprocess_log[:200] if postprocess_log else "")
        if not ok:
            paper_row.update(ok=False, error="postprocess_failed")
            rows_out.append(paper_row)
            continue

        sanitization = parse_report.get("paper2markdown_v3", {})
        paper_row.update(
            {
                "ok": bool(ok and parse_report.get("paper2markdown_v3", {}).get("ok", True)),
                "page_count": parse_report.get("page_count", 0),
                "chunk_count": sanitization.get("chunk_count", 0),
                "assets_kept": sanitization.get("asset_count_model_kept", 0),
                "assets_rejected": sanitization.get("asset_count_rejected", 0),
                "paper_sha256": parse_report.get("pdf_sha256", digest),
                "paper_bytes": parse_report.get("bytes", raw_bytes),
                "output_dir": str(processed_dir),
            }
        )
        if not paper_row["ok"]:
            paper_row["error"] = parse_report.get("error")
        rows_out.append(paper_row)
    return rows_out


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Shared Worker for Paper2Markdown-V3 parsing.")
    parser.add_argument("--run-root", type=Path, default=Path("data/processed_papers"))
    parser.add_argument("--run-name", type=str, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--shard-index", type=int, required=True)
    parser.add_argument("--shard-count", type=int, required=True)
    parser.add_argument("--marker-binary", type=str, default=os.environ.get("MARKER_BIN", "marker_single"))
    parser.add_argument("--marker-timeout-seconds", type=int, default=240)
    parser.add_argument("--max-retries", type=int, default=2)
    parser.add_argument("--worker-id", type=str, default="worker")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    summary = run_worker(
        run_root=args.run_root,
        run_name=args.run_name,
        manifest_path=args.manifest,
        shard_index=args.shard_index,
        shard_count=args.shard_count,
        marker_binary=args.marker_binary,
        marker_timeout_seconds=args.marker_timeout_seconds,
        worker_id=args.worker_id,
        max_retries=args.max_retries,
    )
    run_dir = args.run_root / args.run_name
    report = {
        "run_name": args.run_name,
        "worker_id": args.worker_id,
        "shard_index": args.shard_index,
        "shard_count": args.shard_count,
        "n": len(summary),
        "ok": sum(1 for row in summary if row.get("ok")),
    }
    (run_dir / f"report_shard_{args.shard_index}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (run_dir / f"summary_shard_{args.shard_index}.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in summary),
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
