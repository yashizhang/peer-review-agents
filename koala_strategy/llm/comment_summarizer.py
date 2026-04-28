from __future__ import annotations

import re


def summarize_comment(text: str, max_chars: int = 220) -> str:
    clean = " ".join((text or "").split())
    sentences = re.split(r"(?<=[.!?])\s+", clean)
    for sent in sentences:
        if 40 <= len(sent) <= max_chars:
            return sent
    return clean[:max_chars]

