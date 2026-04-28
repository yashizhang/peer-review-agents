from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from koala_strategy.agent.comment_writer import write_comment
from koala_strategy.agent.github_publisher import publish_reasoning_file
from koala_strategy.agent.harness import run_internal_review_harness
from koala_strategy.agent.lifecycle_policy import can_comment, can_submit_verdict
from koala_strategy.agent.reasoning_writer import write_reasoning_file
from koala_strategy.agent.verdict_writer import prepare_verdict
from koala_strategy.config import load_config
from koala_strategy.data.iclr_loader import load_public_papers
from koala_strategy.data.koala_cache import KoalaCache
from koala_strategy.llm.comment_polisher import polish_comment_draft
from koala_strategy.logging_utils import log_event
from koala_strategy.models.export_bundle import generate_prediction_bundle
from koala_strategy.models.predict_discussion import update_with_discussion
from koala_strategy.models.predict_paper_only import load_model_artifacts
from koala_strategy.platform.koala_client import KoalaClient
from koala_strategy.schemas import CommentRecord, PaperRecord


def _find_public_paper(paper_id: str) -> PaperRecord:
    for paper in load_public_papers():
        if paper.paper_id == paper_id:
            return paper
    raise KeyError(paper_id)


def dry_run_one_paper(paper_id: str, agent_name: str, config: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = config or load_config()
    paper = _find_public_paper(paper_id)
    return process_comment_for_paper(paper, agent_name, cfg, dry_run=True)


def process_comment_for_paper(
    paper: PaperRecord,
    agent_name: str,
    config: dict[str, Any] | None = None,
    dry_run: bool = True,
    client: KoalaClient | None = None,
) -> dict[str, Any]:
    cfg = config or load_config()
    artifacts = load_model_artifacts(cfg)
    bundle = generate_prediction_bundle(paper, artifacts, cfg, save=True)
    harness = run_internal_review_harness(paper, bundle, agent_name, config=cfg)
    content, evidence = write_comment(paper, bundle, agent_name, harness_context=harness)
    content = polish_comment_draft(paper, content, evidence, cfg)
    path = write_reasoning_file(agent_name, paper.paper_id, "comment", content, evidence, cfg)
    url = publish_reasoning_file(agent_name, paper.paper_id, path, cfg)
    cache = KoalaCache(config=cfg)
    duplicate = cache.content_hash_exists(agent_name, paper.paper_id, "comment", content)
    profile = (client or KoalaClient(agent_name, dry_run=False)).get_agent_profile() if not dry_run else {"karma_remaining": 100.0, "karma": 100.0}
    karma = float(profile.get("karma_remaining", profile.get("karma", 100.0)) or 0.0)
    url_ok = url.startswith("http") or (dry_run and url.startswith("dry-run://"))
    ok, reason = can_comment(paper, karma, github_file_url_verified=url_ok, duplicate_content=duplicate)
    action_id = cache.record_action_pending(agent_name, paper.paper_id, "comment", content, 1.0, url)
    posted_id = None
    if ok and not dry_run:
        if not url.startswith("https://github.com/"):
            ok = False
            reason = "real posting requires an https://github.com/... reasoning URL"
        else:
            posted_id = (client or KoalaClient(agent_name, dry_run=False)).post_comment(paper.paper_id, content, url)
    cache.update_action_status(action_id, "dry_run" if ok and dry_run else "success" if ok else "blocked", None if ok else reason, url)
    log_event(
        "actions",
        {
            "agent_name": agent_name,
            "paper_id": paper.paper_id,
            "action_type": "post_comment",
            "dry_run": dry_run,
            "prediction_bundle_path": f"data/koala_cache/predictions/{paper.paper_id}.json",
            "reasoning_file_url": url,
            "comment_id": posted_id,
            "harness_score_hint": harness.get("calibration", {}).get("harness_score_hint"),
            "result": "success" if ok else "blocked",
            "error": None if ok else reason,
        },
        cfg,
    )
    return {
        "paper_id": paper.paper_id,
        "ok": ok,
        "reason": reason,
        "comment_id": posted_id,
        "comment": content,
        "prediction": bundle.model_dump(mode="json"),
        "harness": harness,
        "reasoning_path": str(path),
        "reasoning_url": url,
    }


def mock_external_comments(paper_id: str) -> list[CommentRecord]:
    now = datetime.now(timezone.utc)
    return [
        CommentRecord(
            comment_id="c_ext_positive",
            paper_id=paper_id,
            author_agent="external_calibrator",
            content_markdown="Section 4 and Table 1 provide a concrete positive signal: the method improves the main metric over a relevant baseline, which supports the contribution.",
            created_at=now,
            quality_score=0.82,
        ),
        CommentRecord(
            comment_id="c_ext_rigor",
            paper_id=paper_id,
            author_agent="external_rigor",
            content_markdown="The main concern is experimental rigor: the ablation evidence is limited and Table 2 does not isolate whether each proposed component is necessary.",
            created_at=now,
            quality_score=0.86,
        ),
        CommentRecord(
            comment_id="c_ext_novelty",
            paper_id=paper_id,
            author_agent="external_novelty",
            content_markdown="The novelty claim is plausible but should be calibrated against the closest prior work in Section 2; the distinction is not fully crisp.",
            created_at=now,
            quality_score=0.78,
        ),
        CommentRecord(
            comment_id="c_ext_repro",
            paper_id=paper_id,
            author_agent="external_repro",
            content_markdown="Reproducibility is acceptable if the promised code and hyperparameter details are complete, but the current text leaves some implementation choices implicit.",
            created_at=now,
            quality_score=0.69,
        ),
    ]


def dry_run_verdict(paper_id: str, agent_name: str, config: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = config or load_config()
    artifacts = load_model_artifacts(cfg)
    paper = _find_public_paper(paper_id)
    paper.status = "deliberating"
    bundle = generate_prediction_bundle(paper, artifacts, cfg, save=True)
    harness = run_internal_review_harness(paper, bundle, agent_name, config=cfg)
    comments = mock_external_comments(paper_id)
    discussion_update = update_with_discussion(paper, comments, artifacts)
    draft = prepare_verdict(
        paper,
        bundle,
        comments,
        agent_name,
        same_owner_agent_names=set(),
        discussion_update=discussion_update,
        harness_context=harness,
    )
    evidence = {
        "positive_summary": bundle.paper_only.get("main_positive_evidence", []),
        "negative_summary": bundle.paper_only.get("main_negative_evidence", []),
        "discussion_summary": discussion_update,
        "harness_context": harness,
        "citation_ids": draft.citation_ids,
    }
    path = write_reasoning_file(agent_name, paper.paper_id, "verdict", draft.verdict_markdown, evidence, cfg)
    url = publish_reasoning_file(agent_name, paper.paper_id, path, cfg)
    log_event(
        "verdicts",
        {
            "agent_name": agent_name,
            "paper_id": paper_id,
            "score": draft.score,
            "citation_ids": draft.citation_ids,
            "dry_run": True,
            "reasoning_file_url": url,
            "result": "success",
        },
        cfg,
    )
    return {"paper_id": paper_id, "score": draft.score, "verdict": draft.verdict_markdown, "reasoning_path": str(path), "reasoning_url": url}


def process_verdict_for_paper(
    paper: PaperRecord,
    comments: list[CommentRecord],
    agent_name: str,
    config: dict[str, Any] | None = None,
    dry_run: bool = True,
    client: KoalaClient | None = None,
    agent_commented_in_review: bool = True,
    same_owner_agent_names: set[str] | None = None,
) -> dict[str, Any]:
    cfg = config or load_config()
    same_owner_agent_names = same_owner_agent_names or set()
    artifacts = load_model_artifacts(cfg)
    bundle = generate_prediction_bundle(paper, artifacts, cfg, save=True)
    harness = run_internal_review_harness(paper, bundle, agent_name, config=cfg)
    ok, reason = can_submit_verdict(paper, agent_name, agent_commented_in_review, comments, same_owner_agent_names)
    if not ok:
        return {"paper_id": paper.paper_id, "ok": False, "reason": reason}
    discussion_update = update_with_discussion(paper, comments, artifacts)
    draft = prepare_verdict(
        paper,
        bundle,
        comments,
        agent_name,
        same_owner_agent_names=same_owner_agent_names,
        discussion_update=discussion_update,
        harness_context=harness,
    )
    evidence = {
        "positive_summary": bundle.paper_only.get("main_positive_evidence", []),
        "negative_summary": bundle.paper_only.get("main_negative_evidence", []),
        "discussion_summary": discussion_update,
        "harness_context": harness,
        "citation_ids": draft.citation_ids,
    }
    path = write_reasoning_file(agent_name, paper.paper_id, "verdict", draft.verdict_markdown, evidence, cfg)
    url = publish_reasoning_file(agent_name, paper.paper_id, path, cfg)
    verdict_id = None
    if not dry_run:
        if not url.startswith("https://github.com/"):
            return {"paper_id": paper.paper_id, "ok": False, "reason": "real verdict requires an https://github.com/... reasoning URL"}
        verdict_id = (client or KoalaClient(agent_name, dry_run=False)).submit_verdict(paper.paper_id, draft.score, draft.verdict_markdown, url)
    cache = KoalaCache(config=cfg)
    cache.record_verdict(paper.paper_id, agent_name, draft.score, draft.verdict_markdown, draft.citation_ids, submitted=not dry_run)
    log_event(
        "verdicts",
        {
            "agent_name": agent_name,
            "paper_id": paper.paper_id,
            "score": draft.score,
            "citation_ids": draft.citation_ids,
            "dry_run": dry_run,
            "verdict_id": verdict_id,
            "reasoning_file_url": url,
            "result": "success",
        },
        cfg,
    )
    return {
        "paper_id": paper.paper_id,
        "ok": True,
        "score": draft.score,
        "verdict_id": verdict_id,
        "verdict": draft.verdict_markdown,
        "harness": harness,
        "reasoning_path": str(path),
        "reasoning_url": url,
    }


def run_agent_once(agent_name: str, dry_run: bool = True, limit: int = 5) -> list[dict[str, Any]]:
    cfg = load_config()
    client = KoalaClient(agent_name, dry_run=dry_run)
    papers = client.list_papers(status="in_review")[:limit]
    results = []
    for paper in papers:
        try:
            results.append(process_comment_for_paper(paper, agent_name, cfg, dry_run=dry_run, client=client))
        except Exception as exc:  # noqa: BLE001
            log_event("errors", {"agent_name": agent_name, "paper_id": paper.paper_id, "error": str(exc)}, cfg)
    log_event("heartbeats", {"agent_name": agent_name, "dry_run": dry_run, "processed": len(results)}, cfg)
    return results


def run_verdicts_once(agent_name: str, dry_run: bool = True, limit: int = 5) -> list[dict[str, Any]]:
    cfg = load_config()
    client = KoalaClient(agent_name, dry_run=dry_run)
    papers = client.list_papers(status="deliberating")[:limit]
    results = []
    for paper in papers:
        try:
            comments = mock_external_comments(paper.paper_id) if dry_run else client.get_comments(paper.paper_id)
            commented = True if dry_run else any(c.author_agent == agent_name for c in comments)
            results.append(
                process_verdict_for_paper(
                    paper,
                    comments,
                    agent_name,
                    cfg,
                    dry_run=dry_run,
                    client=client,
                    agent_commented_in_review=commented,
                )
            )
        except Exception as exc:  # noqa: BLE001
            log_event("errors", {"agent_name": agent_name, "paper_id": paper.paper_id, "error": str(exc)}, cfg)
    log_event("heartbeats", {"agent_name": agent_name, "dry_run": dry_run, "processed_verdicts": len(results)}, cfg)
    return results
