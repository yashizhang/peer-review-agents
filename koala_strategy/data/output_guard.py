from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class OutputGuardIssue:
    pattern: str
    reason: str
    snippet: str


# Keep this list precise.  Generic words such as "decision" or "accepted" can
# be legitimate in reviewer prose, so the guard focuses on phrases that usually
# indicate leakage, platform-future information, or accidental disclosure of
# private modelling artifacts.
FORBIDDEN_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\bopenreview\b", "same-paper OpenReview material is forbidden"),
    (r"\bmeta[- ]?review\b", "meta-review information is future leakage"),
    (r"\bofficial reviews?\b", "official reviews are future leakage"),
    (r"\breviewer score(s)?\b", "reviewer scores are future leakage"),
    (r"\bdecision label\b", "decision labels must not appear in public actions"),
    (r"\baccept(?:ed|ance)? status\b", "acceptance status is future leakage"),
    (r"\breject(?:ed|ion)? status\b", "rejection status is future leakage"),
    (r"\bcamera[- ]ready\b", "camera-ready artifacts can leak acceptance status"),
    (r"\bglobal[_-]?test\b", "private evaluation split artifact leaked"),
    (r"\btrain[_-]?test split\b", "private split artifact leaked"),
    (r"\bmodel_[abc]\b", "internal model name leaked into public prose"),
    (r"\bp_accept\b", "internal probability field leaked into public prose"),
    (r"\b\.pkl\b", "model artifact leaked into public prose"),
    (r"\bapi[_-]?key\b", "credential wording leaked"),
)


def _snippet(text: str, start: int, end: int, window: int = 80) -> str:
    lo = max(0, start - window)
    hi = min(len(text), end + window)
    return " ".join(text[lo:hi].split())


def scan_public_output(text: str, extra_patterns: Iterable[tuple[str, str]] | None = None) -> list[OutputGuardIssue]:
    issues: list[OutputGuardIssue] = []
    patterns = list(FORBIDDEN_PATTERNS)
    if extra_patterns:
        patterns.extend(extra_patterns)
    for pattern, reason in patterns:
        for match in re.finditer(pattern, text or "", flags=re.IGNORECASE):
            issues.append(OutputGuardIssue(pattern=pattern, reason=reason, snippet=_snippet(text, match.start(), match.end())))
    return issues


def validate_public_output(text: str, *, block: bool = True) -> tuple[bool, list[OutputGuardIssue]]:
    issues = scan_public_output(text)
    return (not issues or not block), issues


def format_issues(issues: list[OutputGuardIssue]) -> str:
    if not issues:
        return ""
    return "; ".join(f"{issue.reason}: '{issue.snippet}'" for issue in issues[:5])
