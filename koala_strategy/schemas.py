from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class ParsedPaperText(BaseModel):
    title: str | None = None
    abstract: str | None = None
    full_text: str
    sections: dict[str, str] = Field(default_factory=dict)
    page_texts: dict[int, str] = Field(default_factory=dict)
    references: list[str] = Field(default_factory=list)
    figure_captions: list[str] = Field(default_factory=list)
    table_captions: list[str] = Field(default_factory=list)
    table_evidence: list[dict[str, Any]] = Field(default_factory=list)
    source_pdf_path: str | None = None
    parser_warnings: list[str] = Field(default_factory=list)


class PaperRecord(BaseModel):
    paper_id: str
    title: str
    abstract: str | None = None
    full_text: str | None = None
    sections: dict[str, str] = Field(default_factory=dict)
    references: list[str] = Field(default_factory=list)
    domains: list[str] = Field(default_factory=list)
    pdf_url: str | None = None
    code_urls: list[str] = Field(default_factory=list)
    release_time: datetime | None = None
    status: Literal["in_review", "deliberating", "reviewed"] | None = None
    comment_count: int = 0
    participant_count: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)


class ICLRTrainingExample(BaseModel):
    paper_id: str
    title: str
    abstract: str
    full_text: str = ""
    sections: dict[str, str] = Field(default_factory=dict)
    references: list[str] = Field(default_factory=list)
    domains: list[str] = Field(default_factory=list)
    decision: Literal["accept", "reject"]
    official_reviews: list[dict[str, Any]] = Field(default_factory=list)
    meta_review: str | None = None
    review_mean: float | None = None
    review_confidence_mean: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class PseudoReview(BaseModel):
    persona: str
    novelty: int
    technical_soundness: int
    empirical_rigor: int
    clarity: int
    significance: int
    reproducibility: int
    claim_evidence_alignment: int
    missing_baseline_severity: int
    fatal_flaw_severity: int
    fatal_flaws: list[str] = Field(default_factory=list)
    strongest_accept_signal: str
    strongest_reject_signal: str
    confidence: int
    recommended_score_band: str
    short_rationale: str


class PredictionBundle(BaseModel):
    paper_id: str
    domain: str | None = None
    model_version: str
    paper_only: dict[str, Any]
    pseudo_review_panel: dict[str, Any]
    discussion_update: dict[str, Any] | None = None
    agent_instruction: dict[str, Any]


class CommentRecord(BaseModel):
    comment_id: str
    paper_id: str
    author_agent: str
    owner_id: str | None = None
    parent_id: str | None = None
    content_markdown: str
    created_at: datetime
    extracted_claims: list[dict[str, Any]] = Field(default_factory=list)
    quality_score: float | None = None


class DiscussionFeatureBundle(BaseModel):
    paper_id: str
    features: dict[str, float | int]
    extracted_claims: list[dict[str, Any]] = Field(default_factory=list)


class VerdictDraft(BaseModel):
    score: float
    verdict_markdown: str
    citation_ids: list[str]
