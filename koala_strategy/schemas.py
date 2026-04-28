from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from datetime import datetime, timezone
from typing import Any, Literal, TypeVar, get_type_hints

T = TypeVar("T", bound="ModelCompat")


def _parse_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _to_jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if is_dataclass(value):
        return {k: _to_jsonable(v) for k, v in asdict(value).items()}
    if isinstance(value, dict):
        return {str(k): _to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_to_jsonable(v) for v in value]
    return value


class ModelCompat:
    """Small compatibility layer for the subset of Pydantic v2 used here.

    The original project used Pydantic BaseModel.  In the execution environment
    used for this repair, Pydantic on Python 3.13 can hang while constructing
    models.  These dataclasses keep the same project-facing methods used by the
    codebase: model_dump, model_dump_json, model_validate, and
    model_validate_json.
    """

    def model_dump(self, mode: str | None = None) -> dict[str, Any]:
        return _to_jsonable(self)

    def model_dump_json(self, indent: int | None = None) -> str:
        return json.dumps(self.model_dump(mode="json"), ensure_ascii=False, indent=indent, sort_keys=True)

    @classmethod
    def model_validate(cls: type[T], data: Any) -> T:
        if isinstance(data, cls):
            return data
        if not isinstance(data, dict):
            raise TypeError(f"{cls.__name__}.model_validate expects a dict or {cls.__name__}")
        allowed = {f.name for f in fields(cls)}
        kwargs = {k: v for k, v in data.items() if k in allowed}
        return cls(**kwargs)  # type: ignore[arg-type]

    @classmethod
    def model_validate_json(cls: type[T], data: str | bytes) -> T:
        return cls.model_validate(json.loads(data))


@dataclass
class ParsedPaperText(ModelCompat):
    full_text: str = ""
    title: str | None = None
    abstract: str | None = None
    sections: dict[str, str] = field(default_factory=dict)
    page_texts: dict[int, str] = field(default_factory=dict)
    references: list[str] = field(default_factory=list)
    figure_captions: list[str] = field(default_factory=list)
    table_captions: list[str] = field(default_factory=list)
    table_evidence: list[dict[str, Any]] = field(default_factory=list)
    source_pdf_path: str | None = None
    parser_warnings: list[str] = field(default_factory=list)


@dataclass
class PaperRecord(ModelCompat):
    paper_id: str
    title: str
    abstract: str | None = None
    full_text: str | None = None
    sections: dict[str, str] = field(default_factory=dict)
    references: list[str] = field(default_factory=list)
    domains: list[str] = field(default_factory=list)
    pdf_url: str | None = None
    code_urls: list[str] = field(default_factory=list)
    release_time: datetime | None = None
    status: Literal["in_review", "deliberating", "reviewed"] | None = None
    comment_count: int = 0
    participant_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.release_time = _parse_datetime(self.release_time)
        self.comment_count = int(self.comment_count or 0)
        self.participant_count = int(self.participant_count or 0)


@dataclass
class ICLRTrainingExample(ModelCompat):
    paper_id: str
    title: str
    abstract: str
    decision: Literal["accept", "reject"]
    full_text: str = ""
    sections: dict[str, str] = field(default_factory=dict)
    references: list[str] = field(default_factory=list)
    domains: list[str] = field(default_factory=list)
    official_reviews: list[dict[str, Any]] = field(default_factory=list)
    meta_review: str | None = None
    review_mean: float | None = None
    review_confidence_mean: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class PseudoReview(ModelCompat):
    persona: str = "unknown"
    novelty: int = 5
    technical_soundness: int = 5
    empirical_rigor: int = 5
    clarity: int = 5
    significance: int = 5
    reproducibility: int = 5
    claim_evidence_alignment: int = 5
    missing_baseline_severity: int = 2
    fatal_flaw_severity: int = 0
    fatal_flaws: list[str] = field(default_factory=list)
    strongest_accept_signal: str = "No strong accept signal extracted."
    strongest_reject_signal: str = "No strong reject signal extracted."
    confidence: int = 1
    recommended_score_band: str = "borderline"
    short_rationale: str = "Fallback pseudo-review."


@dataclass
class PredictionBundle(ModelCompat):
    paper_id: str
    model_version: str
    paper_only: dict[str, Any]
    pseudo_review_panel: dict[str, Any]
    agent_instruction: dict[str, Any]
    domain: str | None = None
    discussion_update: dict[str, Any] | None = None


@dataclass
class CommentRecord(ModelCompat):
    comment_id: str
    paper_id: str
    author_agent: str
    content_markdown: str
    created_at: datetime
    owner_id: str | None = None
    parent_id: str | None = None
    extracted_claims: list[dict[str, Any]] = field(default_factory=list)
    quality_score: float | None = None

    def __post_init__(self) -> None:
        parsed = _parse_datetime(self.created_at)
        self.created_at = parsed or datetime.now(timezone.utc)
        if self.quality_score is not None:
            self.quality_score = float(self.quality_score)


@dataclass
class DiscussionFeatureBundle(ModelCompat):
    paper_id: str
    features: dict[str, float | int]
    extracted_claims: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class VerdictDraft(ModelCompat):
    score: float
    verdict_markdown: str
    citation_ids: list[str]

    def __post_init__(self) -> None:
        self.score = float(self.score)
