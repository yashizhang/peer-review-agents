from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class GuardResult:
    ok: bool
    reasons: tuple[str, ...] = ()


# These are intentionally conservative leak/signature checks for public Koala
# comments/verdicts.  They avoid blocking normal reviewer terms such as
# "acceptance threshold" or "decision-relevant", but block direct references to
# forbidden future-information channels or internal offline-test artifacts.
FORBIDDEN_PUBLIC_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\bOpenReview\b", "mentions OpenReview"),
    (r"\bmeta[- ]review\b", "mentions meta-review"),
    (r"\bofficial\s+reviews?\b", "mentions official reviews"),
    (r"\bcamera[- ]ready\b", "mentions camera-ready status"),
    (r"\bglobal[_-]?test\b", "mentions hidden/global test artifacts"),
    (r"\btest[_-]?labels?\b", "mentions test labels"),
    (r"\bdecision[_-]?label\b", "mentions decision label"),
    (r"\baccept[_-]?label\b", "mentions accept label"),
    (r"\bsource[_-]?status\b", "mentions source status"),
    (r"\b(?:citation\s+count|citations?\s+trajectory)\b", "mentions citation-count signals"),
    (r"\bGoogle\s+Scholar\b", "mentions Google Scholar"),
    (r"\bSemantic\s+Scholar\b", "mentions Semantic Scholar"),
    (r"\b(?:blog\s+post|social\s+media|Twitter|X\.com|news\s+coverage)\b", "mentions post-publication discussion"),
    (r"\bleaderboard\b", "mentions leaderboard status"),
    (r"\b(?:award|best\s+paper)\b", "mentions award/status information"),
    (r"\b(?:was|is|got|became)\s+(?:accepted|rejected)\b", "states external accept/reject status"),
    (r"\baccepted\s+(?:at|to)\s+\w+\b", "states conference acceptance status"),
    (r"\brejected\s+(?:from|by)\s+\w+\b", "states conference rejection status"),
    (r"\b(?:model_a|model_b|stacker|p_accept|fulltext_evidence_model|\.pkl)\b", "mentions internal model artifact"),
)

NEAR_DUPLICATE_MIN_WORDS = 35


def validate_public_content(text: str, extra_patterns: Iterable[tuple[str, str]] | None = None) -> GuardResult:
    """Return a fail-safe verdict for comment/verdict text before posting.

    The guard is not a substitute for source hygiene; it is the final public-text
    tripwire immediately before writing/publishing/submitting.
    """
    reasons: list[str] = []
    for pattern, reason in (*FORBIDDEN_PUBLIC_PATTERNS, *(tuple(extra_patterns or ()))):
        if re.search(pattern, text, flags=re.IGNORECASE | re.MULTILINE):
            reasons.append(reason)
    if "[[comment:" in text:
        # Verdicts are allowed to cite comments. Comments should not generally do
        # this unless the caller explicitly allows it by not treating this reason
        # as fatal.  The scheduler uses this guard for both; no fatal issue here.
        pass
    return GuardResult(ok=not reasons, reasons=tuple(dict.fromkeys(reasons)))


def validate_github_reasoning_url(url: str, *, allow_dry_run: bool = False) -> GuardResult:
    if allow_dry_run and url.startswith("dry-run://"):
        return GuardResult(True)
    if url.startswith("https://github.com/") or url.startswith("https://raw.githubusercontent.com/"):
        return GuardResult(True)
    return GuardResult(False, ("reasoning URL must be a GitHub blob URL or raw.githubusercontent.com URL",))


def is_near_duplicate_public_text(new_text: str, prior_texts: Iterable[str]) -> bool:
    """Small lexical near-duplicate check for public outputs.

    This complements the exact hash cache and catches accidental template spam.
    """
    new_words = _word_set(new_text)
    if len(new_words) < NEAR_DUPLICATE_MIN_WORDS:
        return False
    for prior in prior_texts:
        old_words = _word_set(prior)
        if len(old_words) < NEAR_DUPLICATE_MIN_WORDS:
            continue
        jaccard = len(new_words & old_words) / max(1, len(new_words | old_words))
        if jaccard >= 0.82:
            return True
    return False


def _word_set(text: str) -> set[str]:
    return {w.lower() for w in re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}", text)}
