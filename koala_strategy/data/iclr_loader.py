from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Literal

from koala_strategy.config import load_config, resolve_path
from koala_strategy.schemas import ICLRTrainingExample, PaperRecord
from koala_strategy.utils import iter_jsonl, normalize_domain, safe_float


ACCEPT_TOKENS = ("accept", "accepted", "poster", "spotlight", "oral")
REJECT_TOKENS = ("reject", "rejected")
DROP_TOKENS = ("withdraw", "desk", "not reviewed", "unknown", "n/a", "none")


def normalize_decision(raw_decision: Any) -> Literal["accept", "reject"] | None:
    if raw_decision is None:
        return None
    if isinstance(raw_decision, list):
        raw_decision = " ".join(str(x) for x in raw_decision)
    text = str(raw_decision).strip().lower()
    if not text:
        return None
    if any(token in text for token in DROP_TOKENS):
        return None
    if any(token in text for token in REJECT_TOKENS):
        return "reject"
    if any(token in text for token in ACCEPT_TOKENS):
        return "accept"
    return None


def read_table(path: str | Path) -> list[dict[str, Any]]:
    p = Path(path)
    if p.suffix == ".jsonl":
        return list(iter_jsonl(p))
    if p.suffix == ".json":
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else list(data.values())
    if p.suffix == ".csv":
        with p.open("r", encoding="utf-8", newline="") as f:
            return list(csv.DictReader(f))
    raise ValueError(f"Unsupported input format: {p}")


def _get(row: dict[str, Any], key: str, default: Any = None) -> Any:
    return row.get(key, default)


def training_example_from_row(row: dict[str, Any], field_map: dict[str, str] | None = None) -> ICLRTrainingExample | None:
    fm = field_map or {}
    paper_id = str(_get(row, fm.get("paper_id", "paper_id"), "")).strip()
    title = str(_get(row, fm.get("title", "title"), "")).strip()
    abstract = str(_get(row, fm.get("abstract", "abstract"), "") or "").strip()
    if not paper_id or not title:
        return None

    decision = normalize_decision(
        row.get("decision_label")
        or row.get("source_status")
        or row.get("decision")
        or row.get(fm.get("decision", "decision"), None)
    )
    if decision is None and "accept_label" in row:
        decision = "accept" if int(row["accept_label"]) == 1 else "reject"
    if decision is None:
        return None

    reviews = row.get(fm.get("reviews", "official_reviews"), row.get("official_reviews", [])) or []
    meta_reviews = row.get(fm.get("meta_review", "meta_reviews"), row.get("meta_reviews", [])) or []
    if isinstance(meta_reviews, list):
        meta_review_text = "\n\n".join(str(m.get("summary", m)) if isinstance(m, dict) else str(m) for m in meta_reviews)
    else:
        meta_review_text = str(meta_reviews) if meta_reviews else None

    review_scores = row.get("review_scores") or {}
    full_text = row.get(fm.get("full_text", "full_text")) or row.get("tldr") or ""
    domains = [normalize_domain(d) or d for d in (row.get("domains") or [])]
    return ICLRTrainingExample(
        paper_id=paper_id,
        title=title,
        abstract=abstract,
        full_text=str(full_text or ""),
        domains=domains,
        decision=decision,
        official_reviews=reviews if isinstance(reviews, list) else [],
        meta_review=meta_review_text,
        review_mean=safe_float(review_scores.get("rating_mean"), default=None),
        review_confidence_mean=safe_float(review_scores.get("confidence_mean"), default=None),
        metadata={k: v for k, v in row.items() if k not in {"official_reviews", "meta_reviews"}},
    )


def load_iclr_examples(path: str | Path | None = None, config: dict[str, Any] | None = None) -> list[ICLRTrainingExample]:
    cfg = config or load_config()
    if path is None:
        dataset_dir = resolve_path(cfg, "koala_dataset_dir")
        candidate = dataset_dir / "global_train.jsonl"
        if candidate.exists():
            path = candidate
        else:
            path = resolve_path(cfg, "iclr_raw_dir") / "papers.jsonl"
    field_map = cfg.get("iclr_field_map", {})
    examples: list[ICLRTrainingExample] = []
    for row in read_table(path):
        ex = training_example_from_row(row, field_map)
        if ex is not None:
            examples.append(ex)
    return examples



def _parse_optional_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _as_string_list(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(x) for x in value if str(x).strip()]
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return []
        if stripped.startswith("["):
            try:
                parsed = json.loads(stripped)
                if isinstance(parsed, list):
                    return [str(x) for x in parsed if str(x).strip()]
            except Exception:
                pass
        return [stripped]
    return [str(value)]

def paper_record_from_row(row: dict[str, Any]) -> PaperRecord:
    pdf_url = row.get("openreview_pdf_url") or row.get("pdf_url_from_note") or row.get("pdf_url") or row.get("pdf")
    status = row.get("status") or row.get("simulated_status")
    if status not in {"in_review", "deliberating", "reviewed"}:
        status = None
    paper_id = str(row.get("paper_id") or row.get("id") or row.get("forum") or row.get("note_id") or "").strip()
    if not paper_id:
        raise KeyError("paper_id")
    domains_raw = _as_string_list(row.get("domains") or row.get("domain_tags") or row.get("tags") or [])
    code_urls = _as_string_list(row.get("github_urls") or row.get("code_urls") or row.get("artifact_urls") or [])
    release_time = None
    for key in ("release_time", "released_at", "created_at", "created", "posted_at"):
        release_time = _parse_optional_datetime(row.get(key))
        if release_time is not None:
            break
    return PaperRecord(
        paper_id=paper_id,
        title=str(row.get("title") or row.get("name") or ""),
        abstract=row.get("abstract") or row.get("summary"),
        full_text=row.get("full_text") or row.get("tldr"),
        domains=[normalize_domain(d) or str(d) for d in domains_raw],
        pdf_url=pdf_url,
        code_urls=code_urls,
        release_time=release_time,
        status=status,
        comment_count=int(row.get("comment_count") or row.get("num_comments") or 0),
        participant_count=int(row.get("participant_count") or row.get("num_participants") or row.get("comment_count") or 0),
        metadata=row,
    )


def load_public_papers(path: str | Path | None = None, config: dict[str, Any] | None = None) -> list[PaperRecord]:
    cfg = config or load_config()
    if path is None:
        dataset_dir = resolve_path(cfg, "koala_dataset_dir")
        path = dataset_dir / "global_test_public.jsonl"
    return [paper_record_from_row(row) for row in read_table(path)]


def load_koala_reference(path: str | Path | None = None, config: dict[str, Any] | None = None) -> list[PaperRecord]:
    cfg = config or load_config()
    if path is None:
        dataset_dir = resolve_path(cfg, "koala_dataset_dir")
        path = dataset_dir / "koala_reference_current.jsonl"
    return [paper_record_from_row(row) for row in read_table(path)]


def load_test_labels(path: str | Path | None = None, config: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    cfg = config or load_config()
    if path is None:
        dataset_dir = resolve_path(cfg, "koala_dataset_dir")
        path = dataset_dir / "global_test_labels.jsonl"
    return {row["paper_id"]: row for row in iter_jsonl(path)}


def iter_training_rows(path: str | Path | None = None, config: dict[str, Any] | None = None) -> Iterable[dict[str, Any]]:
    cfg = config or load_config()
    if path is None:
        path = resolve_path(cfg, "koala_dataset_dir") / "global_train.jsonl"
    yield from iter_jsonl(path)

