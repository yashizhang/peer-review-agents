from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from bs4 import BeautifulSoup

from koala_strategy.paper.text_sanitizer import (
    LINE_NUMBER_LINE_RE,
    POST_DECISION_BLOCK_HEADING_RE,
    SAFE_NEXT_SECTION_RE,
    sanitize_model_text,
)


PAGE_ID_RE = re.compile(r"^/page/(\d+)/")
BODY_START_RE = re.compile(r"(?i)^(abstract|1\s+introduction|introduction)\b")
EXCLUDED_BLOCK_TYPES = {"PageHeader", "PageFooter"}
REFERENCE_SECTION_RE = re.compile(r"(?i)^(references|bibliography)\b")
APPENDIX_SECTION_RE = re.compile(r"(?i)^(appendix|supplementary|[a-z]\s+)")


def marker_page_number(block: Mapping[str, Any], page_count: int) -> int:
    """Return the true 1-based PDF page number from a Marker block id."""

    block_id = str(block.get("id") or "")
    match = PAGE_ID_RE.match(block_id)
    if match:
        page_number = int(match.group(1)) + 1
    else:
        raw_page = block.get("page")
        if not isinstance(raw_page, int):
            raise ValueError(f"Marker block {block_id!r} has no recoverable page number")
        page_number = raw_page

    if page_number < 1 or page_number > page_count:
        raise ValueError(
            f"Marker block {block_id!r} resolved to page {page_number}, outside page_count={page_count}"
        )
    return page_number


def html_to_text(html: str) -> str:
    soup = BeautifulSoup(html or "", "html.parser")
    text = soup.get_text(" ", strip=True)
    return " ".join(text.split())


def is_line_number_text(text: str) -> bool:
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    if not lines:
        return False
    return all(LINE_NUMBER_LINE_RE.fullmatch(line) for line in lines)


def _block_text(block: Mapping[str, Any]) -> str:
    html = block.get("html")
    if isinstance(html, str) and html.strip():
        return html_to_text(html)
    text = block.get("text")
    return " ".join(str(text or "").split())


def _normalize_heading(text: str) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text.strip(" #*_:-")


def _is_body_start_heading(text: str) -> bool:
    return bool(BODY_START_RE.match(_normalize_heading(text)))


def build_marker_v2_chunks(
    marker_payload: Mapping[str, Any],
    *,
    paper_id: str,
    page_count: int,
) -> list[dict[str, Any]]:
    """Build model-facing chunks from Marker blocks with sanitizer and true page refs."""

    blocks = marker_payload.get("blocks") or []
    if not isinstance(blocks, list):
        raise TypeError("marker_payload['blocks'] must be a list")

    chunks: list[dict[str, Any]] = []
    current_section = "Unknown"
    body_started = False
    skipping_unsafe_section = False

    for block in blocks:
        if not isinstance(block, Mapping):
            continue
        block_type = str(block.get("block_type") or "")
        if block_type in EXCLUDED_BLOCK_TYPES:
            continue

        plain = _block_text(block)
        if block_type == "SectionHeader":
            heading = _normalize_heading(plain)
            if _is_body_start_heading(heading):
                body_started = True
            if body_started and POST_DECISION_BLOCK_HEADING_RE.match(heading):
                skipping_unsafe_section = True
                current_section = heading
                continue
            if body_started and skipping_unsafe_section and (
                SAFE_NEXT_SECTION_RE.match(heading) or APPENDIX_SECTION_RE.match(heading)
            ):
                skipping_unsafe_section = False
            if body_started and heading:
                current_section = heading
            continue

        if plain:
            page_number = marker_page_number(block, page_count)
        else:
            page_number = None

        if not body_started:
            continue
        if skipping_unsafe_section:
            continue
        if not plain or is_line_number_text(plain):
            continue

        cleaned = sanitize_model_text(plain)
        if not cleaned or is_line_number_text(cleaned):
            continue

        chunks.append(
            {
                "paper_id": paper_id,
                "chunk_id": f"{paper_id}:{len(chunks):04d}",
                "section": current_section,
                "page_start": page_number,
                "page_end": page_number,
                "type": block_type,
                "text": cleaned,
                "source": "marker_v2",
                "marker_block_id": block.get("id"),
            }
        )

    return chunks


def audit_model_facing_texts(
    artifacts: Mapping[str, str],
    *,
    leak_terms: Sequence[str],
) -> dict[str, Any]:
    hits: dict[str, list[str]] = {}
    lowered = {name: text.lower() for name, text in artifacts.items()}
    for term in leak_terms:
        needle = term.lower()
        if not needle:
            continue
        matched = [name for name, text in lowered.items() if needle in text]
        hits[term] = matched
    return {"ok": not any(hits.values()), "hits": hits, "artifact_count": len(artifacts)}


SOF_MARKERS = {
    0xC0,
    0xC1,
    0xC2,
    0xC3,
    0xC5,
    0xC6,
    0xC7,
    0xC9,
    0xCA,
    0xCB,
    0xCD,
    0xCE,
    0xCF,
}
STANDALONE_MARKERS = {0x01, *range(0xD0, 0xD9)}


def jpeg_dimensions(path: Path) -> tuple[int, int] | None:
    data = Path(path).read_bytes()
    if len(data) < 4 or data[:2] != b"\xff\xd8":
        return None
    idx = 2
    while idx + 3 < len(data):
        if data[idx] != 0xFF:
            idx += 1
            continue
        while idx < len(data) and data[idx] == 0xFF:
            idx += 1
        if idx >= len(data):
            break
        marker = data[idx]
        idx += 1
        if marker in STANDALONE_MARKERS:
            continue
        if idx + 2 > len(data):
            break
        segment_length = int.from_bytes(data[idx : idx + 2], "big")
        if segment_length < 2 or idx + segment_length > len(data):
            break
        if marker in SOF_MARKERS:
            if segment_length < 7:
                return None
            height = int.from_bytes(data[idx + 3 : idx + 5], "big")
            width = int.from_bytes(data[idx + 5 : idx + 7], "big")
            return width, height
        idx += segment_length
    return None


def filter_marker_assets(
    paths: Iterable[Path],
    *,
    min_width: int = 200,
    min_height: int = 120,
    min_area: int = 40_000,
    min_aspect_ratio: float = 0.15,
    max_aspect_ratio: float = 8.0,
) -> list[dict[str, Any]]:
    manifest: list[dict[str, Any]] = []
    for path in sorted(Path(p) for p in paths):
        dimensions = jpeg_dimensions(path)
        width, height = dimensions or (0, 0)
        aspect_ratio = width / height if height else None
        keep = True
        reject_reason = None
        if dimensions is None:
            keep = False
            reject_reason = "unreadable"
        elif aspect_ratio is not None and (
            aspect_ratio < min_aspect_ratio or aspect_ratio > max_aspect_ratio
        ):
            keep = False
            reject_reason = "extreme_aspect_ratio"
        elif width < min_width or height < min_height or width * height < min_area:
            keep = False
            reject_reason = "too_small"

        manifest.append(
            {
                "filename": path.name,
                "path": str(path),
                "bytes": path.stat().st_size,
                "width": width,
                "height": height,
                "aspect_ratio": aspect_ratio,
                "keep": keep,
                "reject_reason": reject_reason,
            }
        )
    return manifest


def split_chunks_by_view(chunks: Sequence[Mapping[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Split chunks into default model, appendix, and reference views."""

    views: dict[str, list[dict[str, Any]]] = {
        "main_body_chunks": [],
        "appendix_chunks": [],
        "reference_chunks": [],
    }
    after_references = False
    for chunk in chunks:
        section = _normalize_heading(str(chunk.get("section") or ""))
        section_key = section.lower()
        if REFERENCE_SECTION_RE.match(section_key):
            view = "reference_chunks"
            after_references = True
        elif after_references or APPENDIX_SECTION_RE.match(section_key):
            view = "appendix_chunks"
        else:
            view = "main_body_chunks"
        views[view].append(dict(chunk))
    return views


def render_model_text_v3(chunks: Sequence[Mapping[str, Any]]) -> str:
    """Render chunks into the default GPT/factsheet text payload."""

    rendered: list[str] = []
    for chunk in chunks:
        page_start = chunk.get("page_start")
        page_end = chunk.get("page_end")
        page = f"p. {page_start}" if page_start == page_end else f"pp. {page_start}-{page_end}"
        section = str(chunk.get("section") or "Unknown")
        block_type = str(chunk.get("type") or "Text")
        text = " ".join(str(chunk.get("text") or "").split())
        if not text:
            continue
        rendered.append(f"[{page} | section: {section} | type: {block_type}]\n{text}")
    return "\n\n".join(rendered)
