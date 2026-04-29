from __future__ import annotations

import csv
import json
from pathlib import Path

from koala_strategy.data.parse_manifests import (
    _parse_review_close_time,
    build_iclr_manifest,
    build_icml_manifest,
)



def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["paper_id", "review_close_time_montreal", "download_url"])
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")


def test_parse_review_close_time_with_montreal_timezone() -> None:
    assert _parse_review_close_time("April 29, 2026 at 4:00:01 PM EDT (Montreal)") == "2026-04-29T16:00:01-04:00"


def test_parse_review_close_time_unknown_timezone_falls_back() -> None:
    assert _parse_review_close_time("April 29, 2026 at 4:00:01 PM XYZ (Montreal)") == "2026-04-29T16:00:01+00:00"


def test_parse_review_close_time_invalid_returns_none() -> None:
    assert _parse_review_close_time("invalid") is None


def test_build_icml_manifest_merges_existing_and_preserves_download_url(tmp_path: Path) -> None:
    csv_path = tmp_path / "due.csv"
    _write_csv(
        csv_path,
        [
            {
                "paper_id": "p1",
                "review_close_time_montreal": "April 29, 2026 at 4:00:01 PM EDT (Montreal)",
                "download_url": "https://koala.science/storage/pdfs/p1.pdf",
            },
            {
                "paper_id": "p2",
                "review_close_time_montreal": "April 29, 2026 at 2:00:01 PM EDT (Montreal)",
                "download_url": "https://example.com/p2.pdf",
            },
        ],
    )
    existing = tmp_path / "existing.json"
    existing.write_text(
        json.dumps(
            [
                {
                    "paper_id": "p1",
                    "download_url": "https://custom.example.com/override.pdf",
                    "source": "old_source",
                    "title": "Existing title",
                }
            ],
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    rows = build_icml_manifest(csv_path, existing_manifest=existing)

    assert rows[0]["paper_id"] == "p1"
    assert rows[0]["download_url"] == "https://custom.example.com/override.pdf"
    assert rows[0]["title"] == "Existing title"
    assert rows[0]["source"] == "old_source"
    assert rows[1]["paper_id"] == "p2"
    assert rows[1]["review_close_time_iso"] == "2026-04-29T14:00:01-04:00"


def test_build_icml_manifest_defaults_to_koala_storage(tmp_path: Path) -> None:
    csv_path = tmp_path / "due.csv"
    _write_csv(
        csv_path,
        [
            {
                "paper_id": "p3",
                "review_close_time_montreal": "April 29, 2026 at 2:00:01 PM EDT (Montreal)",
                "download_url": "",
            }
        ],
    )

    rows = build_icml_manifest(csv_path, existing_manifest=None)
    assert rows[0]["download_url"] == "https://koala.science/storage/pdfs/p3.pdf"


def test_build_icml_manifest_sorts_newest_first(tmp_path: Path) -> None:
    csv_path = tmp_path / "due.csv"
    _write_csv(
        csv_path,
        [
            {
                "paper_id": "old",
                "review_close_time_montreal": "April 28, 2026 at 2:00:01 PM EDT (Montreal)",
            },
            {
                "paper_id": "new",
                "review_close_time_montreal": "April 29, 2026 at 2:00:01 PM EDT (Montreal)",
            },
        ],
    )

    rows = build_icml_manifest(csv_path, existing_manifest=None)
    assert rows[0]["paper_id"] == "new"
    assert rows[1]["paper_id"] == "old"


def test_build_iclr_manifest_prefers_test_rows(tmp_path: Path) -> None:
    train_path = tmp_path / "train.jsonl"
    test_path = tmp_path / "test.jsonl"
    _write_jsonl(
        train_path,
        [
            {
                "paper_id": "shared",
                "title": "train",
                "abstract": "old",
                "openreview_pdf_url": "https://openreview.net/pdf?id=shared_train",
                "source_status": "train",
            },
            {"paper_id": "train_only", "title": "only-train", "openreview_pdf_url": "https://openreview.net/pdf?id=train_only"},
        ],
    )
    _write_jsonl(
        test_path,
        [
            {
                "paper_id": "shared",
                "title": "test",
                "abstract": "new",
                "openreview_pdf_url": "https://openreview.net/pdf?id=shared_test",
                "source_status": "test",
            }
        ],
    )

    rows = build_iclr_manifest(train_path, test_path, prefer_test_first=True)
    assert rows[0]["paper_id"] == "shared"
    assert rows[0]["download_url"] == "https://openreview.net/pdf?id=shared_test"
    assert rows[0]["download_url_from"] == "iclr2026_test_public"
    assert rows[1]["paper_id"] == "train_only"
    assert rows[1]["download_url_from"] == "iclr2026_train"


def test_build_iclr_manifest_falls_back_to_openreview_id(tmp_path: Path) -> None:
    train_path = tmp_path / "train.jsonl"
    _write_jsonl(train_path, [{"paper_id": "no_url", "title": "No Url"}])

    rows = build_iclr_manifest(train_path, None)
    assert rows[0]["download_url"] == "https://openreview.net/pdf?id=no_url"
