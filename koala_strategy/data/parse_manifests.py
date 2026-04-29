from __future__ import annotations

from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from pathlib import Path
import csv
import json
import re
from typing import Any


_KOALA_CSV_TIME_FMT = re.compile(
    r"^(?P<main>.+?)\s+at\s+(?P<time>\d{1,2}:\d{2}:\d{2}\s+(?:AM|PM))\s+(?P<tz>[A-Za-z]+)\s*\(Montreal\)$",
    re.IGNORECASE,
)


def _parse_review_close_time(value: str | None) -> str | None:
    if not value:
        return None
    if re.match(r"^\d{4}-\d{2}-\d{2}T", value):
        return value
    m = _KOALA_CSV_TIME_FMT.match(value)
    if not m:
        return None
    candidate = f"{m.group('main')} at {m.group('time')}"
    for fmt in ["%B %d, %Y at %I:%M:%S %p"]:
        try:
            parsed = datetime.strptime(candidate, fmt)
            break
        except ValueError:
            parsed = None
    else:
        return None
    tz_label = (m.group("tz") or "UTC").upper()
    tz_offset = {
        "EDT": -4,
        "EST": -5,
        "UTC": 0,
        "GMT": 0,
    }
    parsed = parsed.replace(tzinfo=timezone(timedelta(hours=tz_offset.get(tz_label, 0))) )
    return parsed.isoformat()


def _normalise_row(existing: dict[str, Any] | None, updates: dict[str, Any]) -> dict[str, Any]:
    merged = dict(existing or {})
    for key, value in updates.items():
        if value is not None and value != "":
            merged[key] = value
    return merged


def _safe_url_from_record(record: dict[str, Any]) -> str | None:
    for key in (
        "download_url",
        "pdf_url",
        "openreview_pdf_url",
        "pdf_url_from_note",
        "paper_url",
        "url",
    ):
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            if value.startswith("/"):
                return f"https://koala.science{value}"
            if value.startswith("http://") or value.startswith("https://"):
                return value
    return None


def build_icml_manifest(
    csv_path: Path,
    *,
    existing_manifest: Path | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    existing_by_id: dict[str, dict[str, Any]] = {}
    if existing_manifest and existing_manifest.exists():
        existing_rows = json.loads(existing_manifest.read_text(encoding="utf-8"))
        for row in existing_rows:
            if isinstance(row, dict) and row.get("paper_id"):
                existing_by_id[row["paper_id"]] = row

    with csv_path.open("r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            paper_id = (row.get("paper_id") or "").strip()
            if not paper_id:
                continue
            close_time = row.get("review_close_time_montreal")
            iso_time = _parse_review_close_time(close_time)
            base = {
                "paper_id": paper_id,
                "review_close_time_montreal": close_time,
                "review_close_time_iso": iso_time,
            }
            if iso_time:
                base["review_close_time_dt"] = iso_time
            payload = _normalise_row(existing_by_id.get(paper_id), base)
            payload["source"] = payload.get("source", "koala_icml26_due_queue")
            if not payload.get("download_url"):
                payload["download_url"] = f"https://koala.science/storage/pdfs/{paper_id}.pdf"
            rows.append({k: v for k, v in payload.items() if k != "review_close_time_dt"})

    rows.sort(
        key=lambda item: item["review_close_time_iso"] if item.get("review_close_time_iso") else "",
        reverse=True,
    )
    return rows


def build_iclr_manifest(
    train_jsonl: Path,
    test_jsonl: Path | None = None,
    *,
    prefer_test_first: bool = True,
) -> list[dict[str, Any]]:
    def iter_rows(path: Path):
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                payload = json.loads(line)
                if not isinstance(payload, dict):
                    continue
                paper_id = payload.get("paper_id")
                if not paper_id:
                    continue
                yield payload

    sources: list[tuple[str, Path]] = []
    if prefer_test_first:
        if test_jsonl:
            sources.append(("iclr2026_test_public", test_jsonl))
        sources.append(("iclr2026_train", train_jsonl))
    else:
        sources.append(("iclr2026_train", train_jsonl))
        if test_jsonl:
            sources.append(("iclr2026_test_public", test_jsonl))

    manifest_by_id: OrderedDict[str, dict[str, Any]] = OrderedDict()
    for source, path in sources:
        if not path.exists():
            continue
        for row in iter_rows(path):
            paper_id = row["paper_id"]
            if paper_id in manifest_by_id:
                continue
            payload = {
                "paper_id": paper_id,
                "source": source,
                "title": row.get("title"),
                "abstract": row.get("abstract"),
                "domains": row.get("domains") or row.get("keywords"),
                "source_status": row.get("source_status"),
                "source_conference": row.get("source_conference"),
                "download_url": _safe_url_from_record(row),
            }
            if not payload["download_url"]:
                payload["download_url"] = f"https://openreview.net/pdf?id={paper_id}"
            payload["download_url_from"] = source
            manifest_by_id[paper_id] = payload
    return list(manifest_by_id.values())


def dedupe_manifest_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    out: list[dict[str, Any]] = []
    for row in rows:
        paper_id = row.get("paper_id")
        if not paper_id or paper_id in seen:
            continue
        seen.add(paper_id)
        out.append(row)
    return out


def write_manifest(rows: list[dict[str, Any]], path: Path) -> None:
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
