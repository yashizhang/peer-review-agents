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


def test_download_pdf_rejects_non_pdf_payload(tmp_path: Path) -> None:
    destination = tmp_path / "doc.pdf"
    log_path = destination.with_suffix(".pdf.download.log")

    def fake_which(name: str) -> str | None:
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
        if name == "curl":
            return None
        if name == "wget":
            return "/usr/bin/wget"
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
