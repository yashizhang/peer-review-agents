from __future__ import annotations

import tempfile
from pathlib import Path
from urllib.parse import urlparse

import requests

from koala_strategy.paper.section_parser import extract_captions, extract_references, split_sections
from koala_strategy.schemas import ParsedPaperText


def _download_pdf(url: str) -> Path:
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    fd, name = tempfile.mkstemp(suffix=".pdf")
    Path(name).write_bytes(response.content)
    return Path(name)


def _extract_with_pymupdf(path: Path) -> tuple[str, dict[int, str]]:
    import fitz  # type: ignore

    doc = fitz.open(path)
    page_texts: dict[int, str] = {}
    chunks: list[str] = []
    for idx, page in enumerate(doc, start=1):
        text = page.get_text("text")
        page_texts[idx] = text
        chunks.append(f"[Page {idx}]\n{text}")
    return "\n\n".join(chunks), page_texts


def _extract_with_pdfminer(path: Path) -> tuple[str, dict[int, str]]:
    from pdfminer.high_level import extract_text  # type: ignore

    text = extract_text(str(path))
    return text, {1: text}


def parse_pdf_to_text(pdf_path_or_url: str) -> ParsedPaperText:
    source = pdf_path_or_url
    temp_path: Path | None = None
    if urlparse(source).scheme in {"http", "https"}:
        temp_path = _download_pdf(source)
        path = temp_path
    else:
        path = Path(source)
        if not path.exists():
            raise FileNotFoundError(source)

    try:
        try:
            full_text, page_texts = _extract_with_pymupdf(path)
        except Exception:
            full_text, page_texts = _extract_with_pdfminer(path)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)

    sections = split_sections(full_text)
    title = None
    for line in full_text.splitlines():
        clean = line.strip()
        if 8 <= len(clean) <= 220 and not clean.lower().startswith("abstract"):
            title = clean
            break
    abstract = sections.get("abstract")
    return ParsedPaperText(
        title=title,
        abstract=abstract,
        full_text=full_text,
        sections=sections,
        page_texts=page_texts,
        references=extract_references(full_text),
        figure_captions=extract_captions(full_text, "Figure"),
        table_captions=extract_captions(full_text, "Table"),
    )


def parsed_from_record(title: str, abstract: str | None = None, full_text: str | None = None) -> ParsedPaperText:
    text = "\n\n".join(x for x in [title, "Abstract\n" + (abstract or ""), full_text or ""] if x)
    sections = split_sections(text)
    return ParsedPaperText(
        title=title,
        abstract=abstract,
        full_text=text,
        sections=sections,
        references=extract_references(text),
        figure_captions=extract_captions(text, "Figure"),
        table_captions=extract_captions(text, "Table"),
    )

