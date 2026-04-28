from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from koala_strategy.agent.comment_writer import write_comment
from koala_strategy.agent.github_publisher import publish_reasoning_file, verify_github_url
from koala_strategy.agent.harness import run_internal_review_harness
from koala_strategy.agent.lifecycle_policy import can_comment, can_submit_verdict
from koala_strategy.agent.output_guard import validate_github_reasoning_url, validate_public_content
from koala_strategy.agent.paper_selector import compute_paper_utility, should_comment
from koala_strategy.agent.reasoning_writer import write_reasoning_file
from koala_strategy.agent.verdict_writer import prepare_verdict
from koala_strategy.config import effective_config_summary, get_agent_config, load_config, validate_runtime_config
from koala_strategy.data.iclr_loader import load_public_papers
from koala_strategy.data.koala_cache import KoalaCache
from koala_strategy.llm.comment_polisher import polish_comment_draft
from koala_strategy.logging_utils import log_event
from koala_strategy.models.export_bundle import generate_prediction_bundle
from koala_strategy.models.predict_discussion import update_with_discussion
from koala_strategy.models.predict_paper_only import load_model_artifacts
from koala_strategy.platform.koala_client import KoalaClient
from koala_strategy.platform.notifications import deliberating_paper_ids, sync_notifications
from koala_strategy.platform.skill_sync import sync_platform_skill_guidance
from koala_strategy.schemas import CommentRecord, PaperRecord, PredictionBundle


def _find_public_paper(paper_id: str) -> PaperRecord:
    for paper in load_public_papers():
        if paper.paper_id == paper_id:
            return paper
    raise KeyError(paper_id)


def _paper_age_hours(paper: PaperRecord) -> float | None:
    if not paper.release_time:
        return None
    release = paper.release_time if paper.release_time.tzinfo else paper.release_time.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - release).total_seconds() / 3600.0)


def _same_owner_agent_names(config: dict[str, Any], agent_name: str) -> set[str]:
    raw = config.get("competition", {}).get("same_owner_agent_names") or []
    names = {str(x) for x in raw if str(x).strip()}
    # Include all public agents in this repo unless explicitly disabled.  The
    # current strategy normally publishes only review_director, but this prevents
    # accidental self-owned cross-citation when the other personas are enabled.
    if config.get("competition", {}).get("treat_configured_agents_as_same_owner", True):
        names.update(str(x) for x in config.get("agents", {}).keys())
    names.discard(agent_name)
    return names


def _profile_karma(profile: dict[str, Any]) -> float:
    for key in ("karma_remaining", "karma", "karma_balance"):
        if key in profile and profile[key] is not None:
            try:
                return float(profile[key])
            except (TypeError, ValueError):
                pass
    return 100.0


def _agent_comment_count(comments: list[CommentRecord], agent_name: str) -> int:
    return sum(1 for c in comments if c.author_agent == agent_name)


def _verify_reasoning_url(url: str, *, dry_run: bool) -> tuple[bool, str]:
    guard = validate_github_reasoning_url(url, allow_dry_run=dry_run)
    if not guard.ok:
        return False, "; ".join(guard.reasons)
    if dry_run:
        return True, "ok"
    if not verify_github_url(url):
        return False, "reasoning GitHub URL did not return HTTP 200"
    return True, "ok"


def _guard_public_content(content: str) -> tuple[bool, str]:
    result = validate_public_content(content)
    if result.ok:
        return True, "ok"
    return False, "; ".join(result.reasons)


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
    artifacts: dict[str, Any] | None = None,
    prediction: PredictionBundle | None = None,
    existing_comments: list[CommentRecord] | None = None,
) -> dict[str, Any]:
    cfg = config or load_config()
    validate_runtime_config(cfg, agent_name, dry_run=dry_run)
    client = client or KoalaClient(agent_name, dry_run=dry_run, config=cfg)
    cache = KoalaCache(config=cfg)
    artifacts = artifacts or load_model_artifacts(cfg)
    bundle = prediction or generate_prediction_bundle(paper, artifacts, cfg, save=True)
    harness = run_internal_review_harness(paper, bundle, agent_name, config=cfg)
    content, evidence = write_comment(paper, bundle, agent_name, harness_context=harness)
    content = polish_comment_draft(paper, content, evidence, cfg)

    ok, reason = _guard_public_content(content)
    if not ok:
        log_event("actions", {"agent_name": agent_name, "paper_id": paper.paper_id, "action_type": "post_comment", "dry_run": dry_run, "result": "blocked", "error": reason}, cfg)
        return {"paper_id": paper.paper_id, "ok": False, "reason": reason, "comment": content}

    comments = existing_comments if existing_comments is not None else ([] if dry_run else client.get_comments(paper.paper_id))
    for comment in comments:
        cache.upsert_comment(comment)
    agent_comment_count = _agent_comment_count(comments, agent_name)
    local_statuses = ("success", "dry_run") if dry_run else ("success",)
    local_comment_count = cache.count_actions_for_paper(agent_name, paper.paper_id, "comment", statuses=local_statuses)
    already_commented = agent_comment_count > 0 or local_comment_count > 0
    max_comments = int(cfg.get("online_policy", {}).get("max_comments_per_paper_by_agent", 2))
    if max(agent_comment_count, local_comment_count) >= max_comments:
        reason = "max comments per paper by agent reached"
        log_event("actions", {"agent_name": agent_name, "paper_id": paper.paper_id, "action_type": "post_comment", "dry_run": dry_run, "result": "blocked", "error": reason}, cfg)
        return {"paper_id": paper.paper_id, "ok": False, "reason": reason, "comment": content}

    duplicate_statuses = ("pending", "success", "dry_run") if dry_run else ("pending", "success")
    duplicate = cache.content_hash_exists(agent_name, paper.paper_id, "comment", content, statuses=duplicate_statuses)
    profile = client.get_agent_profile() if not dry_run else {"karma_remaining": 100.0, "karma": 100.0}
    karma = _profile_karma(profile)
    # Preflight checks that do not require the GitHub URL yet.
    ok, reason = can_comment(paper, karma, already_commented_on_paper=already_commented, github_file_url_verified=True, duplicate_content=duplicate)
    if not ok:
        log_event("actions", {"agent_name": agent_name, "paper_id": paper.paper_id, "action_type": "post_comment", "dry_run": dry_run, "result": "blocked", "error": reason}, cfg)
        return {"paper_id": paper.paper_id, "ok": False, "reason": reason, "comment": content}

    path = write_reasoning_file(agent_name, paper.paper_id, "comment", content, evidence, cfg)
    url = publish_reasoning_file(agent_name, paper.paper_id, path, cfg)
    url_ok, url_reason = _verify_reasoning_url(url, dry_run=dry_run)
    ok, reason = can_comment(paper, karma, already_commented_on_paper=already_commented, github_file_url_verified=url_ok, duplicate_content=duplicate)
    karma_cost = 0.1 if already_commented else 1.0
    action_id = cache.record_action_pending(agent_name, paper.paper_id, "comment", content, karma_cost, url)
    posted_id = None
    if ok and not dry_run:
        posted_id = client.post_comment(paper.paper_id, content, url)
    final_status = "dry_run" if ok and dry_run else "success" if ok else "blocked"
    final_reason = None if ok else (reason if not url_ok else reason or url_reason)
    cache.update_action_status(action_id, final_status, final_reason, url)
    log_event(
        "actions",
        {
            "agent_name": agent_name,
            "paper_id": paper.paper_id,
            "action_type": "post_comment",
            "dry_run": dry_run,
            "prediction_bundle_path": f"data/koala_cache/predictions/{paper.paper_id}.json",
            "reasoning_file_url": url,
            "reasoning_url_verified": url_ok,
            "comment_id": posted_id,
            "harness_score_hint": harness.get("calibration", {}).get("harness_score_hint"),
            "result": "success" if ok else "blocked",
            "error": final_reason,
        },
        cfg,
    )
    return {
        "paper_id": paper.paper_id,
        "ok": ok,
        "reason": final_reason or reason,
        "comment_id": posted_id,
        "comment": content,
        "prediction": bundle.model_dump(mode="json"),
        "harness": harness,
        "reasoning_path": str(path),
        "reasoning_url": url,
        "reasoning_url_verified": url_ok,
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
        min_comment_quality=float(cfg.get("online_policy", {}).get("min_comment_quality_to_cite", 0.0)),
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
    artifacts: dict[str, Any] | None = None,
    already_submitted: bool | None = None,
) -> dict[str, Any]:
    cfg = config or load_config()
    validate_runtime_config(cfg, agent_name, dry_run=dry_run)
    client = client or KoalaClient(agent_name, dry_run=dry_run, config=cfg)
    cache = KoalaCache(config=cfg)
    same_owner_agent_names = same_owner_agent_names if same_owner_agent_names is not None else _same_owner_agent_names(cfg, agent_name)
    for comment in comments:
        cache.upsert_comment(comment)
    if already_submitted is None:
        already_submitted = cache.has_submitted_verdict(paper.paper_id, agent_name) or (False if dry_run else client.has_submitted_verdict(paper.paper_id))
    artifacts = artifacts or load_model_artifacts(cfg)
    bundle = generate_prediction_bundle(paper, artifacts, cfg, save=True)
    harness = run_internal_review_harness(paper, bundle, agent_name, config=cfg)
    min_quality = float(cfg.get("online_policy", {}).get("min_comment_quality_to_cite", 0.0))
    ok, reason = can_submit_verdict(
        paper,
        agent_name,
        agent_commented_in_review,
        comments,
        same_owner_agent_names,
        already_submitted=already_submitted,
        min_citations=int(cfg.get("competition", {}).get("min_external_citations_for_verdict", 3)),
        min_comment_quality=min_quality,
    )
    if not ok:
        log_event("verdicts", {"agent_name": agent_name, "paper_id": paper.paper_id, "dry_run": dry_run, "result": "blocked", "error": reason}, cfg)
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
        min_comment_quality=min_quality,
    )
    ok, reason = _guard_public_content(draft.verdict_markdown)
    if not ok:
        log_event("verdicts", {"agent_name": agent_name, "paper_id": paper.paper_id, "dry_run": dry_run, "result": "blocked", "error": reason}, cfg)
        return {"paper_id": paper.paper_id, "ok": False, "reason": reason, "verdict": draft.verdict_markdown}
    evidence = {
        "positive_summary": bundle.paper_only.get("main_positive_evidence", []),
        "negative_summary": bundle.paper_only.get("main_negative_evidence", []),
        "discussion_summary": discussion_update,
        "harness_context": harness,
        "citation_ids": draft.citation_ids,
    }
    path = write_reasoning_file(agent_name, paper.paper_id, "verdict", draft.verdict_markdown, evidence, cfg)
    url = publish_reasoning_file(agent_name, paper.paper_id, path, cfg)
    url_ok, url_reason = _verify_reasoning_url(url, dry_run=dry_run)
    if not url_ok:
        log_event("verdicts", {"agent_name": agent_name, "paper_id": paper.paper_id, "dry_run": dry_run, "result": "blocked", "error": url_reason, "reasoning_file_url": url}, cfg)
        return {"paper_id": paper.paper_id, "ok": False, "reason": url_reason, "reasoning_path": str(path), "reasoning_url": url}
    verdict_id = None
    if not dry_run:
        verdict_id = client.submit_verdict(paper.paper_id, draft.score, draft.verdict_markdown, url)
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
            "reasoning_url_verified": url_ok,
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
        "reasoning_url_verified": url_ok,
    }


def _rank_comment_candidates(
    papers: list[PaperRecord],
    agent_name: str,
    cfg: dict[str, Any],
    client: KoalaClient,
    dry_run: bool,
    artifacts: dict[str, Any],
    karma: float,
) -> list[dict[str, Any]]:
    cache = KoalaCache(config=cfg)
    agent_cfg = get_agent_config(cfg, agent_name)
    roster = [agent_name]  # keep one public agent by default to avoid same-owner citation games
    ranked: list[dict[str, Any]] = []
    policy = cfg.get("online_policy", {})
    scan_limit = int(policy.get("selector_scan_limit", 80))
    for paper in papers[:scan_limit]:
        try:
            cache.upsert_paper(paper)
            comments = [] if dry_run else client.get_comments(paper.paper_id, limit=100)
            for comment in comments:
                cache.upsert_comment(comment)
            local_statuses = ("success", "dry_run") if dry_run else ("success",)
            already = _agent_comment_count(comments, agent_name) > 0 or cache.count_actions_for_paper(agent_name, paper.paper_id, "comment", statuses=local_statuses) > 0
            prediction = generate_prediction_bundle(paper, artifacts, cfg, save=True)
            ok, reason = should_comment(
                paper,
                prediction,
                agent_name,
                already_commented_by_agent=already,
                current_karma=karma,
                agents=roster,
                paper_age_hours=_paper_age_hours(paper),
                config=cfg,
            )
            if not ok:
                log_event("paper_selection", {"agent_name": agent_name, "paper_id": paper.paper_id, "selected": False, "reason": reason}, cfg)
                continue
            utility = compute_paper_utility(paper, prediction, agent_name, str(agent_cfg.get("focus", "balanced")), karma)
            ranked.append({"paper": paper, "prediction": prediction, "comments": comments, "utility": utility})
            log_event("paper_selection", {"agent_name": agent_name, "paper_id": paper.paper_id, "selected": True, "utility": utility}, cfg)
        except Exception as exc:  # noqa: BLE001
            log_event("errors", {"agent_name": agent_name, "paper_id": paper.paper_id, "stage": "paper_selection", "error": str(exc)}, cfg)
    ranked.sort(key=lambda row: float(row["utility"]), reverse=True)
    return ranked


def run_agent_once(agent_name: str, dry_run: bool | None = None, limit: int = 5) -> list[dict[str, Any]]:
    cfg = load_config()
    dry = bool(cfg.get("online_policy", {}).get("dry_run", True) if dry_run is None else dry_run)
    validate_runtime_config(cfg, agent_name, dry_run=dry)
    log_event("effective_config", effective_config_summary(cfg, agent_name, dry_run=dry), cfg)
    sync_platform_skill_guidance(cfg)
    client = KoalaClient(agent_name, dry_run=dry, config=cfg)
    notifications = sync_notifications(client, agent_name, cfg)
    profile = client.get_agent_profile() if not dry else {"karma_remaining": 100.0, "karma": 100.0}
    karma = _profile_karma(profile)
    artifacts = load_model_artifacts(cfg)
    papers = client.list_papers(status="in_review", limit=int(cfg.get("online_policy", {}).get("selector_fetch_limit", 300)))
    ranked = _rank_comment_candidates(papers, agent_name, cfg, client, dry, artifacts, karma)
    results: list[dict[str, Any]] = []
    policy = cfg.get("online_policy", {})
    max_today = int(policy.get("max_first_comments_per_agent_per_day", 30))
    max_per_run = int(policy.get("max_comments_per_run", limit))
    run_limit = min(limit, max_today, max_per_run)
    for row in ranked[:run_limit]:
        paper = row["paper"]
        try:
            results.append(
                process_comment_for_paper(
                    paper,
                    agent_name,
                    cfg,
                    dry_run=dry,
                    client=client,
                    artifacts=artifacts,
                    prediction=row.get("prediction"),
                    existing_comments=row.get("comments"),
                )
            )
        except Exception as exc:  # noqa: BLE001
            log_event("errors", {"agent_name": agent_name, "paper_id": paper.paper_id, "stage": "process_comment", "error": str(exc)}, cfg)
    log_event(
        "heartbeats",
        {
            "agent_name": agent_name,
            "dry_run": dry,
            "processed": len(results),
            "candidate_count": len(ranked),
            "notifications_seen": len(notifications),
        },
        cfg,
    )
    return results


def run_verdicts_once(agent_name: str, dry_run: bool | None = None, limit: int = 5) -> list[dict[str, Any]]:
    cfg = load_config()
    dry = bool(cfg.get("online_policy", {}).get("dry_run", True) if dry_run is None else dry_run)
    validate_runtime_config(cfg, agent_name, dry_run=dry)
    log_event("effective_config", effective_config_summary(cfg, agent_name, dry_run=dry), cfg)
    sync_platform_skill_guidance(cfg)
    client = KoalaClient(agent_name, dry_run=dry, config=cfg)
    notifications = sync_notifications(client, agent_name, cfg)
    notified_ids = set(deliberating_paper_ids(notifications))
    papers = client.list_papers(status="deliberating", limit=int(cfg.get("online_policy", {}).get("selector_fetch_limit", 300)))
    # Pull notification-triggered papers to the front if they are in the fetched set.
    papers.sort(key=lambda p: 0 if p.paper_id in notified_ids else 1)
    artifacts = load_model_artifacts(cfg)
    same_owner = _same_owner_agent_names(cfg, agent_name)
    results: list[dict[str, Any]] = []
    cache = KoalaCache(config=cfg)
    max_per_run = int(cfg.get("online_policy", {}).get("max_verdicts_per_run", limit))
    run_limit = min(limit, max_per_run)
    for paper in papers[:run_limit]:
        try:
            comments = mock_external_comments(paper.paper_id) if dry else client.get_comments(paper.paper_id)
            commented = True if dry else any(c.author_agent == agent_name for c in comments) or cache.successful_action_exists(agent_name, paper.paper_id, "comment", statuses=("success",))
            results.append(
                process_verdict_for_paper(
                    paper,
                    comments,
                    agent_name,
                    cfg,
                    dry_run=dry,
                    client=client,
                    agent_commented_in_review=commented,
                    same_owner_agent_names=same_owner,
                    artifacts=artifacts,
                )
            )
        except Exception as exc:  # noqa: BLE001
            log_event("errors", {"agent_name": agent_name, "paper_id": paper.paper_id, "stage": "process_verdict", "error": str(exc)}, cfg)
    log_event("heartbeats", {"agent_name": agent_name, "dry_run": dry, "processed_verdicts": len(results), "notifications_seen": len(notifications)}, cfg)
    return results
