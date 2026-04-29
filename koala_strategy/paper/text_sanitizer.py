from __future__ import annotations

import re
from typing import Any


POST_DECISION_LINE_RE = re.compile(
    r"(?i)(acknowledg|author contribution|corresponding author|equal contribution|affiliation|"
    r"anonymous authors?|openreview|accepted|published|camera[- ]?ready|under review|submitted|submission|"
    r"conference paper|paper decision|iclr\s*2026|work done during|rebuttal|withdrawn|"
    r"code release|trademarks? of|all rights reserved|contemplated purchases|"
    r"(?:<sup|lt;sup|\^\{|\$\^).*(?:university|institute|corporation|mila|cifar|chair)|"
    r"^project page:|^code:|^contact:)"
)
EMAIL_RE = re.compile(r"(?i)\b[\w.+-]+@[\w.-]+\.[a-z]{2,}\b")
URL_RE = re.compile(r"(?i)\bhttps?://\S+")
POST_DECISION_BLOCK_HEADING_RE = re.compile(
    r"(?im)^\s*(?:#{1,6}\s*)?(?:\*\*)?(?:\d+(?:\.\d+)*\s+)?"
    r"(acknowledg(?:e)?ments?|author contributions?|funding|checklist|"
    r"camera[- ]ready changes?|rebuttal|ethics statement|broader impact statement)\b.*$"
)
SAFE_NEXT_SECTION_RE = re.compile(
    r"(?im)^\s*(?:#{1,6}\s*)?(?:\*\*)?(?:\d+(?:\.\d+)*\s+)?"
    r"(abstract|introduction|background|related work|method|methods|approach|experiments?|results|discussion|limitations?|conclusion|references|appendix)\b.*$"
)
MARKDOWN_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s*")
HTML_TAG_RE = re.compile(r"<[^>]+>")
ABSTRACT_HEADING_RE = re.compile(r"(?i)^abstract\b")


SAFE_SECTION_KEYS = [
    "abstract",
    "introduction",
    "background",
    "related_work",
    "method",
    "methods",
    "approach",
    "experiments",
    "experiment",
    "results",
    "discussion",
    "limitations",
    "limitation",
    "conclusion",
]


def remove_post_decision_blocks(text: str) -> str:
    raw = text or ""
    matches = list(POST_DECISION_BLOCK_HEADING_RE.finditer(raw))
    if not matches:
        return raw
    ranges: list[tuple[int, int]] = []
    for match in matches:
        next_match = SAFE_NEXT_SECTION_RE.search(raw, match.end())
        end = next_match.start() if next_match else len(raw)
        ranges.append((match.start(), end))
    keep: list[str] = []
    cursor = 0
    for start, end in ranges:
        if start > cursor:
            keep.append(raw[cursor:start])
        cursor = max(cursor, end)
    keep.append(raw[cursor:])
    return "".join(keep)


def _plain_heading_text(line: str) -> str:
    plain = HTML_TAG_RE.sub(" ", line.strip())
    plain = MARKDOWN_HEADING_RE.sub("", plain)
    plain = plain.replace("**", " ").replace("__", " ").replace("`", " ")
    plain = re.sub(r"\s+", " ", plain)
    return plain.strip(" #*_:-")


def remove_pre_abstract_identity_block(text: str) -> str:
    """Drop PDF/Marker title-page author metadata while retaining title and paper body."""

    raw = text or ""
    lines = raw.splitlines()
    abstract_idx: int | None = None
    for idx, line in enumerate(lines):
        if ABSTRACT_HEADING_RE.match(_plain_heading_text(line)):
            abstract_idx = idx
            break
    if abstract_idx is None:
        return raw

    title_line = ""
    for line in lines[:abstract_idx]:
        candidate = _plain_heading_text(line)
        if candidate and not POST_DECISION_LINE_RE.search(candidate) and not EMAIL_RE.search(candidate):
            title_line = candidate
            break

    body = "\n".join(lines[abstract_idx:]).strip()
    if not title_line:
        return body
    return f"{title_line}\n\n{body}"


def sanitize_model_text(text: str, max_chars: int | None = None) -> str:
    """Remove obvious post-decision/version metadata before modeling or LLM use.

    This is intentionally conservative: lines that look like venue/status,
    author identity, email, acknowledgement, project/contact metadata, or
    OpenReview artifacts are removed. URLs are stripped but the surrounding
    sentence is retained when it is otherwise paper content.
    """

    text = remove_pre_abstract_identity_block(text or "")
    text = remove_post_decision_blocks(text)
    cleaned_lines: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if POST_DECISION_LINE_RE.search(line) or EMAIL_RE.search(line):
            continue
        line = URL_RE.sub(" ", line)
        line = " ".join(line.split())
        if line:
            cleaned_lines.append(line)
    cleaned = "\n".join(cleaned_lines)
    if max_chars is not None and len(cleaned) > max_chars:
        head = cleaned[: int(max_chars * 0.75)]
        tail = cleaned[-int(max_chars * 0.25) :]
        cleaned = head + "\n...\n" + tail
    return cleaned


def sanitized_sections_text(sections: dict[str, Any], max_chars_per_section: int = 10000) -> str:
    selected: list[str] = []
    for key in SAFE_SECTION_KEYS:
        value = sections.get(key)
        if value:
            cleaned = sanitize_model_text(str(value), max_chars=max_chars_per_section)
            if cleaned:
                selected.append(f"{key.replace('_', ' ').title()}\n{cleaned}")
    return "\n\n".join(selected)


def sanitized_fulltext_payload(payload: dict[str, Any], mode: str) -> str:
    title = sanitize_model_text(str(payload.get("title") or ""), max_chars=500)
    abstract = sanitize_model_text(str(payload.get("abstract") or ""), max_chars=2500)
    sections = payload.get("sections") or {}
    table_text = sanitize_model_text(
        "\n".join(str(t.get("caption_or_context", "")) for t in payload.get("table_evidence") or []),
        max_chars=35000,
    )
    figure_text = sanitize_model_text("\n".join(payload.get("figure_captions") or []), max_chars=20000)
    full_text = sanitize_model_text(str(payload.get("full_text") or ""), max_chars=80000)
    section_text = sanitized_sections_text(sections, max_chars_per_section=12000)
    if mode == "tables":
        return "\n\n".join(x for x in [title, abstract, table_text] if x)
    if mode == "evidence_tables":
        return "\n\n".join(x for x in [title, abstract, table_text] if x)
    if mode == "evidence":
        return "\n\n".join(x for x in [title, abstract, table_text, figure_text] if x)
    if mode == "sections":
        return "\n\n".join(x for x in [title, abstract, section_text, table_text] if x)
    if mode == "full":
        return "\n\n".join(x for x in [title, abstract, full_text, table_text] if x)
    if mode == "full_sections":
        return "\n\n".join(x for x in [title, abstract, section_text, full_text, table_text] if x)
    return "\n\n".join(x for x in [title, abstract, table_text] if x)
