from __future__ import annotations

import re


SECTION_RE = re.compile(
    r"(?im)^(?:\d+(?:\.\d+)*\s+)?(abstract|introduction|background|related work|method|methods|approach|experiments?|results|discussion|limitations?|conclusion|references|appendix)\b.*$"
)


def split_sections(text: str) -> dict[str, str]:
    matches = list(SECTION_RE.finditer(text or ""))
    if not matches:
        return {}
    sections: dict[str, str] = {}
    for idx, match in enumerate(matches):
        name = match.group(1).lower().replace(" ", "_")
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        if body:
            sections[name] = body[:60000]
    return sections


def extract_references(text: str) -> list[str]:
    sections = split_sections(text)
    refs = sections.get("references", "")
    if not refs:
        parts = re.split(r"(?im)^references\b", text or "", maxsplit=1)
        refs = parts[1] if len(parts) == 2 else ""
    entries = re.split(r"\n\s*(?:\[\d+\]|\d+\.|\w[\w-]+,\s)", refs)
    clean = [" ".join(e.split()) for e in entries if len(e.split()) >= 5]
    return clean[:300]


def extract_captions(text: str, kind: str) -> list[str]:
    pattern = re.compile(rf"(?is)\b{kind}\s+\d+[:.]\s*(.*?)(?=\n\s*(?:Figure|Table)\s+\d+[:.]|\n\s*\d+\s+[A-Z]|\Z)")
    return [" ".join(m.group(0).split())[:1000] for m in pattern.finditer(text or "")][:100]

