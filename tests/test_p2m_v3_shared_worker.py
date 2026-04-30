from __future__ import annotations

import hashlib
from pathlib import Path

from scripts.jobs import p2m_v3_shared_worker as worker


def test_download_pdf_with_curl_saves_pdf_and_writes_log(tmp_path: Path) -> None:
    destination = tmp_path / "doc.pdf"
    pdf_bytes = b"%PDF-1.4\nsample\n"
    expected = hashlib.sha256(pdf_bytes).hexdigest()
    log_path = destination.with_suffix(".pdf.download.log")

    def fake_which(name: str) -> str | None:
        if name == "wget":
            return "/usr/bin/wget"
        if name == "curl":
            return "/usr/bin/curl"
        return None

    def fake_run(command: list[str], timeout: int | None = None) -> tuple[int, str]:
        destination.write_bytes(pdf_bytes)
        return 0, "download ok"

    monkey = __import__("pytest").MonkeyPatch()
    monkey.setattr(worker.shutil, "which", fake_which)
    monkey.setattr(worker, "_run_command", fake_run)
    with monkey.context():
        ok, bytes_count, digest = worker._download_pdf(
            "https://example.com/doc.pdf",
            destination,
            timeout_seconds=1,
            download_log=log_path,
        )
    assert ok
    assert bytes_count == len(pdf_bytes)
    assert digest == expected
    assert destination.read_bytes() == pdf_bytes
    assert log_path.exists()
    assert "command:" in log_path.read_text()


def test_download_pdf_prefers_wget_when_available(tmp_path: Path) -> None:
    destination = tmp_path / "doc.pdf"
    log_path = destination.with_suffix(".pdf.download.log")

    def fake_which(name: str) -> str | None:
        if name == "wget":
            return "/usr/bin/wget"
        if name == "curl":
            return "/usr/bin/curl"
        return None

    used_command: list[str] = []

    def fake_run(command: list[str], timeout: int | None = None) -> tuple[int, str]:
        used_command.extend(command)
        destination.write_bytes(b"%PDF-1.4\n")
        return 0, "download ok"

    monkey = __import__("pytest").MonkeyPatch()
    monkey.setattr(worker.shutil, "which", fake_which)
    monkey.setattr(worker, "_run_command", fake_run)
    with monkey.context():
        worker._download_pdf(
            "https://example.com/doc.pdf",
            destination,
            timeout_seconds=1,
            download_log=log_path,
        )
    assert any(item.endswith("wget") for item in used_command)


def test_download_pdf_rejects_non_pdf_payload(tmp_path: Path) -> None:
    destination = tmp_path / "doc.pdf"
    log_path = destination.with_suffix(".pdf.download.log")

    def fake_which(name: str) -> str | None:
        if name == "wget":
            return "/usr/bin/wget"
        if name == "curl":
            return "/usr/bin/curl"
        return None

    def fake_run(command: list[str], timeout: int | None = None) -> tuple[int, str]:
        destination.write_bytes(b"<html><body>blocked</body></html>")
        return 0, "download ok"

    monkey = __import__("pytest").MonkeyPatch()
    monkey.setattr(worker.shutil, "which", fake_which)
    monkey.setattr(worker, "_run_command", fake_run)
    with monkey.context():
        ok, bytes_count, digest = worker._download_pdf(
            "https://example.com/doc.pdf",
            destination,
            timeout_seconds=1,
            download_log=log_path,
        )
    assert not ok
    assert bytes_count == 0
    assert digest is None
    assert not destination.exists()
    assert "not a PDF" in log_path.read_text()


def test_download_pdf_falls_back_to_wget(tmp_path: Path) -> None:
    destination = tmp_path / "doc.pdf"
    pdf_bytes = b"%PDF-1.7\nfrom-wget\n"
    log_path = destination.with_suffix(".pdf.download.log")

    def fake_which(name: str) -> str | None:
        if name == "wget":
            return "/usr/bin/wget"
        if name == "curl":
            return "/usr/bin/curl"
        return None

    def fake_run(command: list[str], timeout: int | None = None) -> tuple[int, str]:
        destination.write_bytes(pdf_bytes)
        return 0, "download ok"

    monkey = __import__("pytest").MonkeyPatch()
    monkey.setattr(worker.shutil, "which", fake_which)
    monkey.setattr(worker, "_run_command", fake_run)
    with monkey.context():
        ok, bytes_count, digest = worker._download_pdf(
            "https://example.com/doc.pdf",
            destination,
            timeout_seconds=1,
            download_log=log_path,
        )
    assert ok
    assert bytes_count == len(pdf_bytes)
    assert hashlib.sha256(pdf_bytes).hexdigest() == digest
    assert "wget" in log_path.read_text()


def test_run_postprocess_reports_failed_summary(tmp_path: Path, monkeypatch) -> None:
    def fake_run(command: list[str], timeout: int | None = None) -> tuple[int, str]:
        return 0, '{"summary_path": "summary.json", "papers": 1, "ok": 0}'

    monkeypatch.setattr(worker, "_run_command", fake_run)

    ok, _ = worker.run_postprocess("paper-1", tmp_path / "marker_raw", tmp_path / "processed_v3")

    assert ok is False


def test_ensure_parse_report_creates_parent_directory(tmp_path: Path) -> None:
    paper_root = tmp_path / "marker_raw" / "paper-1" / "marker_markdown" / "paper-1"
    pdf_path = tmp_path / "paper-1.pdf"
    pdf_path.write_bytes(b"%PDF-1.7\n")

    worker._ensure_parse_report(
        paper_root,
        paper_id="paper-1",
        pdf_path=pdf_path,
        download_url="https://openreview.net/pdf?id=paper-1",
        parse_ok=True,
        page_count=3,
        sha256="abc123",
        bytes_count=9,
        elapsed_seconds=1.5,
    )

    assert (paper_root / "parse_report.json").exists()
