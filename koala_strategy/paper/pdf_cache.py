from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any


from koala_strategy.config import load_config, project_root
from koala_strategy.paper.section_parser import extract_captions, extract_references, split_sections
from koala_strategy.paper.table_evidence import extract_table_evidence_from_text
from koala_strategy.schemas import ParsedPaperText
from koala_strategy.utils import ensure_dir


LEAKY_LINE_RE = re.compile(
    r"(?i)(published as a conference paper|accepted at|under review|submitted to|iclr\s*2026|conference paper at|anonymous authors|paper decision|work done during|corresponding author|equal contribution|^project page:|^[\\w.+-]+@[\\w.-]+)"
)


def sanitize_pdf_text(text: str, known_title: str | None = None, known_abstract: str | None = None) -> tuple[str, list[str]]:
    warnings: list[str] = []
    lines = []
    removed = 0
    for line in (text or "").splitlines():
        if LEAKY_LINE_RE.search(line):
            removed += 1
            continue
        lines.append(line)
    cleaned = "\n".join(lines)
    if removed:
        warnings.append(f"removed_{removed}_venue_or_status_lines")

    abstract_match = re.search(r"(?im)^abstract\b", cleaned)
    if abstract_match:
        # Drop title/author/header block before Abstract. Reinsert public title and
        # public abstract to keep the model aligned with Koala-visible metadata.
        body = cleaned[abstract_match.start() :]
        cleaned = "\n\n".join(x for x in [known_title or "", body] if x)
        warnings.append("dropped_pre_abstract_header")
    elif known_abstract:
        cleaned = "\n\n".join(x for x in [known_title or "", "Abstract\n" + known_abstract, cleaned] if x)
        warnings.append("abstract_heading_not_found")
    return cleaned, warnings


def pdf_cache_paths(paper_id: str, config: dict[str, Any] | None = None) -> tuple[Path, Path]:
    cfg = config or load_config()
    root = project_root() / "data" / "pdf_cache"
    raw = ensure_dir(root / "raw") / f"{paper_id}.pdf"
    parsed = ensure_dir(root / "parsed") / f"{paper_id}.json"
    return raw, parsed


def download_pdf(url: str, out_path: Path, retries: int = 3) -> bool:
    ensure_dir(out_path.parent)
    if out_path.exists() and out_path.stat().st_size > 1000:
        return True
    headers = {"User-Agent": "koala-strategy-pdf-cache/0.1"}
    last_error = None
    for attempt in range(retries):
        try:
            import requests

            response = requests.get(url, timeout=45, headers=headers)
            response.raise_for_status()
            if not response.content.startswith(b"%PDF"):
                raise ValueError("response is not a PDF")
            out_path.write_bytes(response.content)
            return True
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            time.sleep(0.7 * (attempt + 1))
    _ = last_error
    return False


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def parse_pdf_file(pdf_path: Path, paper_id: str, title: str, abstract: str | None = None) -> ParsedPaperText:
    import fitz  # type: ignore

    doc = fitz.open(pdf_path)
    page_texts: dict[int, str] = {}
    chunks: list[str] = []
    for idx, page in enumerate(doc, start=1):
        text = page.get_text("text")
        page_texts[idx] = text
        chunks.append(f"[Page {idx}]\n{text}")
    raw_text = "\n\n".join(chunks)
    cleaned, warnings = sanitize_pdf_text(raw_text, title, abstract)
    sections = split_sections(cleaned)
    table_evidence = extract_table_evidence_from_text(cleaned)
    return ParsedPaperText(
        title=title,
        abstract=abstract or sections.get("abstract"),
        full_text=cleaned,
        sections=sections,
        page_texts=page_texts,
        references=extract_references(cleaned),
        figure_captions=extract_captions(cleaned, "Figure"),
        table_captions=extract_captions(cleaned, "Table"),
        table_evidence=table_evidence,
        source_pdf_path=str(pdf_path),
        parser_warnings=warnings,
    )


def parse_and_cache_pdf_record(record: dict[str, Any], config: dict[str, Any] | None = None, force: bool = False) -> dict[str, Any]:
    paper_id = str(record.get("paper_id") or record.get("id"))
    title = str(record.get("title") or "")
    abstract = record.get("abstract")
    url = record.get("openreview_pdf_url") or record.get("pdf_url_from_note") or record.get("pdf_url")
    raw_path, parsed_path = pdf_cache_paths(paper_id, config)
    if parsed_path.exists() and not force:
        try:
            cached = json.loads(parsed_path.read_text(encoding="utf-8"))
            if cached.get("ok"):
                return cached
        except json.JSONDecodeError:
            pass
    result: dict[str, Any] = {
        "paper_id": paper_id,
        "title": title,
        "abstract": abstract,
        "ok": False,
        "error": None,
        "parsed_path": str(parsed_path),
        "pdf_path": str(raw_path),
    }
    if not url:
        result["error"] = "missing_pdf_url"
        _write_json_atomic(parsed_path, result)
        return result
    try:
        if not str(url).startswith("http"):
            result["error"] = "relative_pdf_url_not_downloadable"
            _write_json_atomic(parsed_path, result)
            return result
        if not download_pdf(str(url), raw_path):
            result["error"] = "download_failed"
            _write_json_atomic(parsed_path, result)
            return result
        parsed = parse_pdf_file(raw_path, paper_id, title, abstract)
        payload = parsed.model_dump(mode="json")
        payload.update({"paper_id": paper_id, "ok": True, "parsed_path": str(parsed_path), "pdf_path": str(raw_path)})
        _write_json_atomic(parsed_path, payload)
        return payload
    except Exception as exc:  # noqa: BLE001
        result["error"] = str(exc)[:500]
        _write_json_atomic(parsed_path, result)
        return result


def batch_parse_pdf_records(
    records: list[dict[str, Any]],
    workers: int = 6,
    force: bool = False,
    config: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(parse_and_cache_pdf_record, record, config, force) for record in records]
        for future in as_completed(futures):
            out.append(future.result())
    return out
