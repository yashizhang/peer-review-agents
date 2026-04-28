from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from koala_strategy.config import load_config, project_root
from koala_strategy.discussion.citation_selector import select_citations
from koala_strategy.discussion.claim_extractor import extract_claim
from koala_strategy.llm.json_guard import extract_json_object
from koala_strategy.llm.providers import get_text_provider
from koala_strategy.llm.review_judge import llm_evidence_payload
from koala_strategy.models.score_mapping import probability_to_quality_percentile, percentile_to_koala_score, shrink_score_for_uncertainty
from koala_strategy.schemas import CommentRecord, PaperRecord, PredictionBundle, VerdictDraft
from koala_strategy.utils import clamp, content_hash, dump_json, ensure_dir


PROMPT_VERSION = "agentic_reviewer_v1"


def agentic_llm_enabled(config: dict[str, Any] | None = None) -> bool:
    cfg = config or load_config()
    models = cfg.get("models", {})
    agentic = models.get("agentic_llm", {}) or {}
    provider = str(models.get("llm_provider", "heuristic")).lower()
    return bool(agentic.get("enabled", True)) and provider != "heuristic"


def _agentic_cfg(config: dict[str, Any]) -> dict[str, Any]:
    defaults = {
        "enabled": True,
        "triage_enabled": True,
        "comment_writer_enabled": True,
        "discussion_synthesis_enabled": True,
        "verdict_writer_enabled": True,
        "self_critique_enabled": True,
        "max_llm_triage_candidates": 12,
        "triage_temperature": 0.1,
        "comment_temperature": 0.2,
        "discussion_temperature": 0.1,
        "verdict_temperature": 0.15,
        "self_critique_temperature": 0.0,
        "llm_judge_score_weight": 0.72,
        "final_score_llm_weight": 0.78,
        "max_score_deviation_from_tool_prior": 2.2,
        "cache_dir": "data/agentic_llm_cache",
        "max_comments_for_discussion_prompt": 24,
        "min_comment_markdown_chars": 700,
        "max_comment_markdown_chars": 2600,
    }
    raw = config.get("models", {}).get("agentic_llm", {}) or {}
    out = {**defaults, **raw}
    return out


def _model(config: dict[str, Any]) -> str:
    return str(config.get("models", {}).get("codex_model", "gpt-5.4-mini"))


def _cache_path(kind: str, key: str, prompt: str, config: dict[str, Any]) -> Path:
    root = Path(_agentic_cfg(config).get("cache_dir") or "data/agentic_llm_cache")
    if not root.is_absolute():
        root = project_root() / root
    digest = content_hash(f"{PROMPT_VERSION}|{_model(config)}|{kind}|{key}|{prompt}")[:20]
    return root / kind / f"{key}_{digest}.json"


def _trim(text: str, max_chars: int = 1200) -> str:
    clean = " ".join(str(text or "").split())
    if len(clean) <= max_chars:
        return clean
    head = clean[: int(max_chars * 0.72)].rstrip()
    tail = clean[-int(max_chars * 0.22) :].lstrip()
    return f"{head} ... {tail}"


def _as_float(value: Any, default: float, lo: float = 0.0, hi: float = 1.0) -> float:
    try:
        return float(clamp(float(value), lo, hi))
    except (TypeError, ValueError):
        return float(default)


def _as_list(value: Any, limit: int = 8) -> list[str]:
    if isinstance(value, list):
        raw = value
    elif value is None:
        raw = []
    else:
        raw = [value]
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        text = _trim(str(item), 500)
        key = text.lower()
        if text and key not in seen:
            out.append(text)
            seen.add(key)
        if len(out) >= limit:
            break
    return out


def _call_json_task(
    kind: str,
    key: str,
    prompt: str,
    config: dict[str, Any],
    *,
    fallback: dict[str, Any],
    temperature: float = 0.0,
    force: bool = False,
) -> dict[str, Any]:
    path = _cache_path(kind, key, prompt, config)
    if path.exists() and not force:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
    provider = get_text_provider(config)
    data: dict[str, Any]
    try:
        raw = provider.generate(prompt, model=_model(config), temperature=temperature)
        data = extract_json_object(raw)
        data["fallback"] = False
        data["raw_response_hash"] = content_hash(raw)
    except Exception as exc:  # noqa: BLE001
        data = {**fallback, "fallback": True, "error": str(exc)[:400]}
    data["prompt_version"] = PROMPT_VERSION
    data["model"] = _model(config)
    data["cache_path"] = str(path)
    dump_json(path, data)
    return data


def _paper_context_for_prompt(paper: PaperRecord, prediction: PredictionBundle) -> dict[str, Any]:
    payload = llm_evidence_payload(paper, prediction)
    # Keep the planner/reviewer informed, but avoid duplicating huge excerpts in
    # every prompt.  The evidence payload is already sanitized by review_judge.
    return {
        "paper_id": paper.paper_id,
        "title": paper.title,
        "abstract": payload.get("abstract", ""),
        "domains": paper.domains,
        "non_llm_tool_prior": payload.get("non_llm_prior", {}),
        "reviewer_component_diagnostics": payload.get("reviewer_component_diagnostics", []),
        "tables": payload.get("tables", [])[:6],
        "selected_sections": _trim(str(payload.get("selected_sections", "")), 16000),
        "sanitized_full_text_excerpt": _trim(str(payload.get("sanitized_full_text_excerpt", "")), 18000),
    }


def _koala_agent_background() -> str:
    return """Koala Science background:
- You are one public review agent among many agents discussing active ML papers.
- Karma is scarce budget. A first comment costs karma, follow-ups cost karma, and low-value generic comments waste the budget.
- The agent wins by making specific, useful, evidence-grounded contributions that help later verdicts, not by sounding enthusiastic.
- The non-LLM model prior is a tool for calibration. It is not ground truth and must not be revealed publicly.
- Public text must never mention hidden labels, model internals, official reviews, decisions, acceptance status, citation counts, or later impact."""


def _domain_lens_for_prompt(domains: list[str] | None) -> str:
    joined = " ".join(domains or [])
    lenses: list[str] = []
    if any(x in joined for x in ["Theory", "Optimization"]):
        lenses.append("theory/optimization: check theorem assumptions, proof-claim fit, meaningful regimes, and whether experiments are optional or central")
    if any(x in joined for x in ["NLP", "LLM-Alignment", "Generative-Models"]):
        lenses.append("NLP/LLM/generative: check baseline strength, contamination/data provenance, ablations, robustness, judge validity, and compute disclosure")
    if any(x in joined for x in ["Computer-Vision", "Robotics", "Reinforcement-Learning"]):
        lenses.append("vision/robotics/RL: check benchmark/task diversity, seed/statistical reliability, real-vs-sim scope, ablations, and failure modes")
    if any(x in joined for x in ["Healthcare-Science-Applications", "Trustworthy-ML"]):
        lenses.append("science/trustworthy ML: check threat model or deployment claim, external validity, uncertainty, ethics/privacy, and failure analysis")
    if not lenses:
        lenses.append("general ML: check claim-evidence alignment, novelty, baselines, ablations, robustness, reproducibility, and honest limitations")
    return "Domain expert lenses to consult without overfitting to one task:\n" + "\n".join(f"- {x}" for x in lenses[:3])


def _comment_summaries(comments: list[CommentRecord], current_agent: str, same_owner: set[str] | None = None, limit: int = 24) -> list[dict[str, Any]]:
    same_owner = same_owner or set()
    rows: list[dict[str, Any]] = []
    for c in comments:
        if not c.comment_id or c.author_agent == current_agent or c.author_agent in same_owner:
            continue
        claim = extract_claim(c.comment_id, c.author_agent, c.content_markdown, c.owner_id)
        rows.append(
            {
                "comment_id": c.comment_id,
                "author_agent": c.author_agent,
                "quality_score": float(c.quality_score if c.quality_score is not None else claim.get("quality_score", 0.0)),
                "polarity": claim.get("polarity"),
                "decision_relevance": claim.get("decision_relevance"),
                "specificity": claim.get("specificity"),
                "content_excerpt": _trim(c.content_markdown, 1200),
            }
        )
    rows.sort(key=lambda r: (float(r.get("quality_score", 0.0)), float(r.get("decision_relevance", 0.0)), float(r.get("specificity", 0.0))), reverse=True)
    return rows[:limit]


def _fallback_triage(paper: PaperRecord, prediction: PredictionBundle, base_utility: float) -> dict[str, Any]:
    p = float(prediction.paper_only.get("p_accept", 0.5))
    uncertainty = float(prediction.paper_only.get("uncertainty", 0.5))
    positive = prediction.paper_only.get("main_positive_evidence", []) or []
    negative = prediction.paper_only.get("main_negative_evidence", []) or []
    evidence_quality = clamp(0.25 + 0.12 * len(positive) + 0.12 * len(negative), 0.0, 1.0)
    return {
        "should_comment": evidence_quality >= 0.35 and base_utility > 0.0,
        "comment_worthiness": float(clamp(0.45 + 0.35 * abs(p - 0.5) + 0.15 * evidence_quality, 0.0, 1.0)),
        "specific_evidence_quality": float(evidence_quality),
        "likely_discussion_value": float(clamp(0.35 + 0.2 * paper.participant_count + 0.2 * evidence_quality, 0.0, 1.0)),
        "risk_of_generic_comment": float(clamp(0.7 - evidence_quality, 0.0, 1.0)),
        "utility_multiplier": 1.0,
        "utility_bonus": 0.0,
        "use_traditional_prior_weight": float(clamp(0.3 + 0.5 * (1.0 - uncertainty), 0.15, 0.85)),
        "comment_angle": "Audit the strongest paper-internal support and the most decision-relevant remaining risk.",
        "claim_to_audit": _trim(paper.abstract or paper.title, 280),
        "evidence_to_use": _as_list(positive, 4),
        "concerns_to_raise": _as_list(negative, 4),
        "tool_plan": [
            {"tool": "non_llm_prior", "decision": "use_as_calibration_anchor", "weight": 0.45, "why": "Fallback triage used the statistical prior and extracted paper evidence."},
            {"tool": "paper_evidence", "decision": "primary_public_basis", "weight": 0.55, "why": "Public comments must be grounded in paper-internal evidence."},
        ],
        "confidence": float(clamp(1.0 - uncertainty, 0.1, 0.8)),
        "rationale": "Fallback triage because agentic LLM output was unavailable or invalid.",
        "fallback": True,
    }


def validate_triage(data: dict[str, Any], paper: PaperRecord, prediction: PredictionBundle, base_utility: float) -> dict[str, Any]:
    fb = _fallback_triage(paper, prediction, base_utility)
    out = {**fb, **data}
    out["should_comment"] = bool(out.get("should_comment", fb["should_comment"]))
    out["comment_worthiness"] = _as_float(out.get("comment_worthiness"), fb["comment_worthiness"])
    out["specific_evidence_quality"] = _as_float(out.get("specific_evidence_quality"), fb["specific_evidence_quality"])
    out["likely_discussion_value"] = _as_float(out.get("likely_discussion_value"), fb["likely_discussion_value"])
    out["risk_of_generic_comment"] = _as_float(out.get("risk_of_generic_comment"), fb["risk_of_generic_comment"])
    out["utility_multiplier"] = _as_float(out.get("utility_multiplier"), 1.0, 0.35, 1.75)
    out["utility_bonus"] = _as_float(out.get("utility_bonus"), 0.0, -3.0, 3.0)
    out["use_traditional_prior_weight"] = _as_float(out.get("use_traditional_prior_weight"), fb["use_traditional_prior_weight"], 0.0, 1.0)
    out["comment_angle"] = _trim(str(out.get("comment_angle") or fb["comment_angle"]), 500)
    out["claim_to_audit"] = _trim(str(out.get("claim_to_audit") or fb["claim_to_audit"]), 500)
    out["evidence_to_use"] = _as_list(out.get("evidence_to_use") or fb["evidence_to_use"], 6)
    out["concerns_to_raise"] = _as_list(out.get("concerns_to_raise") or fb["concerns_to_raise"], 6)
    out["confidence"] = _as_float(out.get("confidence"), fb["confidence"], 0.0, 1.0)
    out["rationale"] = _trim(str(out.get("rationale") or fb["rationale"]), 800)
    if not isinstance(out.get("tool_plan"), list):
        out["tool_plan"] = fb["tool_plan"]
    adjusted = float(base_utility) * float(out["utility_multiplier"]) + float(out["utility_bonus"])
    adjusted += 1.2 * float(out["comment_worthiness"]) + 0.9 * float(out["specific_evidence_quality"])
    adjusted -= 0.9 * float(out["risk_of_generic_comment"])
    out["base_utility"] = float(base_utility)
    out["adjusted_utility"] = float(adjusted)
    return out


def run_llm_paper_triage(
    paper: PaperRecord,
    prediction: PredictionBundle,
    comments: list[CommentRecord],
    agent_name: str,
    base_utility: float,
    config: dict[str, Any] | None = None,
    *,
    force: bool = False,
) -> dict[str, Any]:
    cfg = config or load_config()
    if not agentic_llm_enabled(cfg):
        return validate_triage({"fallback": True}, paper, prediction, base_utility)
    agentic = _agentic_cfg(cfg)
    context = _paper_context_for_prompt(paper, prediction)
    comment_context = _comment_summaries(comments, agent_name, set(), limit=8)
    fallback = _fallback_triage(paper, prediction, base_utility)
    prompt = f"""You are the chief planner of a Koala Science peer-review agent.

{_koala_agent_background()}

{_domain_lens_for_prompt(paper.domains)}

Your job is to decide whether this agent should spend karma on a public comment for this paper. You may use the non-LLM prior and heuristic diagnostics as tools, but the final action choice is yours. Prioritize comments that will be paper-specific, useful to later verdicts, and likely to improve scientific discussion. Do not use any future same-paper information such as official reviews, decisions, citation counts, social media, or later impact.

Return ONLY one valid JSON object with this exact schema:
{{
  "should_comment": true,
  "comment_worthiness": 0.0,
  "specific_evidence_quality": 0.0,
  "likely_discussion_value": 0.0,
  "risk_of_generic_comment": 0.0,
  "utility_multiplier": 1.0,
  "utility_bonus": 0.0,
  "use_traditional_prior_weight": 0.0,
  "comment_angle": "...",
  "claim_to_audit": "...",
  "evidence_to_use": ["..."],
  "concerns_to_raise": ["..."],
  "tool_plan": [{{"tool": "non_llm_prior|table_evidence|section_evidence|discussion_context", "decision": "use|ignore|downweight|request_more_evidence", "weight": 0.0, "why": "..."}}],
  "confidence": 0.0,
  "rationale": "..."
}}

Scales are 0 to 1 except utility_multiplier and utility_bonus. Keep utility_multiplier between 0.35 and 1.75, utility_bonus between -3 and 3. Set should_comment=false if the best available comment would be generic, low-evidence, or duplicative.

Paper and tool context:
{json.dumps({"paper": context, "platform_context": {"status": paper.status, "comment_count": paper.comment_count, "participant_count": paper.participant_count, "base_utility": base_utility}, "existing_discussion_excerpt": comment_context}, ensure_ascii=False, sort_keys=True)}
"""
    raw = _call_json_task(
        "triage",
        paper.paper_id,
        prompt,
        cfg,
        fallback=fallback,
        temperature=float(agentic.get("triage_temperature", 0.1)),
        force=force,
    )
    return validate_triage(raw, paper, prediction, base_utility)


def _fallback_comment(paper: PaperRecord, prediction: PredictionBundle, harness_context: dict[str, Any] | None, triage: dict[str, Any] | None, agent_name: str) -> dict[str, Any]:
    positives = _as_list((harness_context or {}).get("positive_evidence") or prediction.paper_only.get("main_positive_evidence") or [], 5)
    negatives = _as_list((harness_context or {}).get("negative_evidence") or prediction.paper_only.get("main_negative_evidence") or [], 5)
    claim = _trim((triage or {}).get("claim_to_audit") or paper.abstract or paper.title, 420)
    positive = positives[0] if positives else "The paper reports a concrete contribution and gives some supporting evidence."
    negative = negatives[0] if negatives else "The central unresolved issue is whether the strongest claim is supported under fair comparisons and clear scope limits."
    angle = _trim((triage or {}).get("comment_angle") or "decision-relevant evidence audit", 260)
    content = f"""**Decision-relevant audit: {angle}**

**Claim I audited.** {claim}

**Paper-internal support.** {positive} This is the main reason I would consider moving the paper upward, provided the comparison and protocol are sound.

**Main risk.** {negative} This risk is decision-relevant because it determines whether the paper's central contribution is supported rather than merely plausible.

**What would change my view.** I would update upward if another reviewer verifies that the key comparison, ablation, or theoretical link directly supports the central claim; I would update downward if that evidence is missing, narrow, or confounded.

**Current stance.** This is a discussion note, not a final verdict. I am using the statistical prior only as a calibration tool and grounding the public claim in paper-internal evidence.
"""
    return {
        "content_markdown": content.strip() + "\n",
        "claim_checked": claim,
        "paper_internal_support": positives,
        "main_risk": negatives,
        "how_to_update": ["Verify the key comparison/protocol against the central claim."],
        "used_tools": ["paper_internal_evidence", "non_llm_prior_as_calibration_tool"],
        "confidence": float(clamp(1.0 - float(prediction.paper_only.get("uncertainty", 0.5)), 0.1, 0.8)),
        "rationale": "Fallback agentic comment generated from extracted evidence.",
        "fallback": True,
    }


def validate_comment_plan(data: dict[str, Any], paper: PaperRecord, prediction: PredictionBundle, harness_context: dict[str, Any] | None, triage: dict[str, Any] | None, agent_name: str, config: dict[str, Any]) -> dict[str, Any]:
    fb = _fallback_comment(paper, prediction, harness_context, triage, agent_name)
    out = {**fb, **data}
    content = str(out.get("content_markdown") or "").strip()
    agentic = _agentic_cfg(config)
    min_chars = int(agentic.get("min_comment_markdown_chars", 700))
    max_chars = int(agentic.get("max_comment_markdown_chars", 2600))
    if len(content) < min_chars:
        content = fb["content_markdown"].strip()
        out["fallback"] = True
        out["fallback_reason"] = f"LLM comment was too short ({len(content)} chars after fallback check)."
    if len(content) > max_chars:
        content = content[: max_chars - 80].rstrip() + "\n\n[Truncated for focus.]"
    out["content_markdown"] = content.strip() + "\n"
    out["claim_checked"] = _trim(str(out.get("claim_checked") or fb["claim_checked"]), 600)
    out["paper_internal_support"] = _as_list(out.get("paper_internal_support") or fb["paper_internal_support"], 6)
    out["main_risk"] = _as_list(out.get("main_risk") or fb["main_risk"], 6)
    out["how_to_update"] = _as_list(out.get("how_to_update") or fb["how_to_update"], 5)
    out["used_tools"] = _as_list(out.get("used_tools") or fb["used_tools"], 8)
    out["confidence"] = _as_float(out.get("confidence"), fb["confidence"], 0.0, 1.0)
    out["rationale"] = _trim(str(out.get("rationale") or fb["rationale"]), 900)
    return out


def compose_llm_public_comment(
    paper: PaperRecord,
    prediction: PredictionBundle,
    harness_context: dict[str, Any] | None,
    triage: dict[str, Any] | None,
    agent_name: str,
    config: dict[str, Any] | None = None,
    *,
    force: bool = False,
) -> tuple[str, dict[str, Any]]:
    cfg = config or load_config()
    if not agentic_llm_enabled(cfg) or not bool(_agentic_cfg(cfg).get("comment_writer_enabled", True)):
        plan = _fallback_comment(paper, prediction, harness_context, triage, agent_name)
    else:
        agentic = _agentic_cfg(cfg)
        context = _paper_context_for_prompt(paper, prediction)
        prompt = f"""You are the lead LLM reviewer inside a Koala Science agent. Write the public comment this agent should submit.

{_koala_agent_background()}

{_domain_lens_for_prompt(paper.domains)}

Use only the paper-internal evidence and tool outputs below. The traditional model prior, component diagnostics, and table parser are tools; you may downweight or override them, but do not mention hidden probabilities, model names, training labels, or internal artifacts in the public text. Do not use official reviews, decisions, acceptance status, citation counts, later impact, social media, or OpenReview discussion.

The public comment should be specific enough for other agents to cite later. It should not be a generic accept/reject prediction. It should identify a concrete claim, the best paper-internal support, a decision-relevant risk, and what evidence would change the assessment. Do not fabricate section/table numbers; only name a section/table if it appears in the evidence context.

Return ONLY one valid JSON object with this schema:
{{
  "content_markdown": "markdown comment",
  "claim_checked": "...",
  "paper_internal_support": ["..."],
  "main_risk": ["..."],
  "how_to_update": ["..."],
  "used_tools": ["paper evidence", "non-LLM prior as calibration", "table parser", "discussion context"],
  "confidence": 0.0,
  "rationale": "private rationale for reasoning file"
}}

Recommended style:
- 4 to 6 concise markdown sections.
- No score, no official-looking verdict, no final decision language.
- State uncertainty explicitly when evidence is thin.
- Make the comment useful for a future verdict.

Context:
{json.dumps({"paper": context, "harness_context": harness_context or {}, "llm_triage_plan": triage or {}, "agent_name": agent_name}, ensure_ascii=False, sort_keys=True)}
"""
        fb = _fallback_comment(paper, prediction, harness_context, triage, agent_name)
        raw = _call_json_task(
            "comment",
            paper.paper_id,
            prompt,
            cfg,
            fallback=fb,
            temperature=float(agentic.get("comment_temperature", 0.2)),
            force=force,
        )
        plan = validate_comment_plan(raw, paper, prediction, harness_context, triage, agent_name, cfg)
    evidence = {
        "paper_id": paper.paper_id,
        "agent": agent_name,
        "agentic_llm_comment": True,
        "claim_checked": plan.get("claim_checked"),
        "positive_evidence": plan.get("paper_internal_support", []),
        "negative_evidence": plan.get("main_risk", []),
        "how_to_update": plan.get("how_to_update", []),
        "used_tools": plan.get("used_tools", []),
        "llm_triage": triage or {},
        "harness_context": harness_context or {},
        "comment_writer_private_rationale": plan.get("rationale"),
        "llm_confidence": plan.get("confidence"),
        "prediction_summary": {
            "claim_checked": plan.get("claim_checked"),
            "paper_internal_support": plan.get("paper_internal_support", []),
            "main_risk": plan.get("main_risk", []),
            "how_to_update": plan.get("how_to_update", []),
            "used_tools": plan.get("used_tools", []),
            "triage_summary": {
                "comment_angle": (triage or {}).get("comment_angle"),
                "tool_plan": (triage or {}).get("tool_plan", []),
                "should_comment": (triage or {}).get("should_comment"),
            },
        },
    }
    return str(plan["content_markdown"]), evidence


def self_critique_public_output(
    kind: str,
    paper: PaperRecord,
    content_markdown: str,
    evidence: dict[str, Any] | None,
    config: dict[str, Any] | None = None,
    *,
    required_comment_refs: list[str] | None = None,
    force: bool = False,
) -> dict[str, Any]:
    cfg = config or load_config()
    required_comment_refs = required_comment_refs or []
    if not agentic_llm_enabled(cfg) or not bool(_agentic_cfg(cfg).get("self_critique_enabled", True)):
        return {"approved": True, "revised_markdown": content_markdown, "quality_score": 0.65, "risk_flags": [], "changes": [], "fallback": True}
    agentic = _agentic_cfg(cfg)
    fallback = {"approved": True, "revised_markdown": content_markdown, "quality_score": 0.65, "risk_flags": [], "changes": [], "fallback": True}
    prompt = f"""You are the safety and quality critic for a Koala Science review agent.

Review this proposed public {kind}. Your task is to make a minimal revision only if needed. Check that it is paper-specific, scientifically useful, not generic, not insulting, and not using prohibited future information. Do not add new unsupported facts. Do not remove required comment references.

Forbidden future same-paper signals include official reviews, scores, meta-reviews, decisions, acceptance status, citation counts, awards, social media, news, or later impact. Also remove internal model names, hidden probabilities, training/test labels, and file artifact names.

Return ONLY one JSON object:
{{
  "approved": true,
  "revised_markdown": "...",
  "quality_score": 0.0,
  "risk_flags": ["..."],
  "changes": ["..."]
}}

Required comment refs that must remain if present: {json.dumps(required_comment_refs, ensure_ascii=False)}
Paper title: {paper.title}
Evidence summary:
{json.dumps(evidence or {}, ensure_ascii=False, sort_keys=True)[:9000]}
Proposed public markdown:
{content_markdown}
"""
    raw = _call_json_task(
        f"self_critique_{kind}",
        paper.paper_id,
        prompt,
        cfg,
        fallback=fallback,
        temperature=float(agentic.get("self_critique_temperature", 0.0)),
        force=force,
    )
    revised = str(raw.get("revised_markdown") or content_markdown).strip() + "\n"
    for ref in required_comment_refs:
        token = f"[[comment:{ref}]]"
        if token not in revised:
            revised = revised.rstrip() + f"\n\nRequired discussion reference retained: {token}\n"
    return {
        "approved": bool(raw.get("approved", True)),
        "revised_markdown": revised,
        "quality_score": _as_float(raw.get("quality_score"), 0.65, 0.0, 1.0),
        "risk_flags": _as_list(raw.get("risk_flags"), 12),
        "changes": _as_list(raw.get("changes"), 12),
        "fallback": bool(raw.get("fallback", False)),
        "model": raw.get("model"),
        "cache_path": raw.get("cache_path"),
    }


def _fallback_discussion_synthesis(
    comments: list[CommentRecord],
    agent_name: str,
    same_owner_agent_names: set[str],
    min_comment_quality: float,
    min_citations: int,
) -> dict[str, Any]:
    citations = select_citations(comments, agent_name, same_owner_agent_names, min_citations=min_citations, max_citations=5, min_quality=min_comment_quality)
    ids = [c.comment_id for c in citations]
    return {
        "citation_ids": ids,
        "supporting_comment_ids": ids[:2],
        "concerning_comment_ids": ids[2:3],
        "discussion_strength_accept": 0.5,
        "discussion_strength_reject": 0.5,
        "score_delta": 0.0,
        "accept_probability_delta": 0.0,
        "synthesis_markdown": "Fallback synthesis: selected the highest-quality eligible external comments with distinct authors.",
        "confidence": 0.35,
        "rationale": "Fallback discussion synthesis.",
        "fallback": True,
    }


def validate_discussion_synthesis(
    data: dict[str, Any],
    comments: list[CommentRecord],
    agent_name: str,
    same_owner_agent_names: set[str],
    min_comment_quality: float,
    min_citations: int,
) -> dict[str, Any]:
    fallback = _fallback_discussion_synthesis(comments, agent_name, same_owner_agent_names, min_comment_quality, min_citations)
    eligible: dict[str, CommentRecord] = {}
    authors: dict[str, str] = {}
    for c in comments:
        if not c.comment_id or c.author_agent == agent_name or c.author_agent in same_owner_agent_names:
            continue
        claim = extract_claim(c.comment_id, c.author_agent, c.content_markdown, c.owner_id)
        quality = float(c.quality_score if c.quality_score is not None else claim.get("quality_score", 0.0))
        if quality < min_comment_quality:
            continue
        eligible[c.comment_id] = c
        authors[c.comment_id] = c.author_agent
    raw_ids = data.get("citation_ids") if isinstance(data.get("citation_ids"), list) else []
    chosen: list[str] = []
    chosen_authors: set[str] = set()
    for raw in raw_ids:
        cid = str(raw)
        if cid in eligible and authors[cid] not in chosen_authors:
            chosen.append(cid)
            chosen_authors.add(authors[cid])
        if len(chosen) >= 5:
            break
    for cid in fallback["citation_ids"]:
        if cid in eligible and authors[cid] not in chosen_authors:
            chosen.append(cid)
            chosen_authors.add(authors[cid])
        if len(chosen) >= max(5, min_citations):
            break
    if len(chosen_authors) < min_citations:
        # Let the caller surface the citation error. This should be rare because
        # can_submit_verdict has already checked the same eligibility condition.
        chosen = fallback["citation_ids"]
    out = {**fallback, **data}
    out["citation_ids"] = chosen[:5]
    out["supporting_comment_ids"] = [x for x in _as_list(out.get("supporting_comment_ids"), 5) if x in eligible][:5]
    out["concerning_comment_ids"] = [x for x in _as_list(out.get("concerning_comment_ids"), 5) if x in eligible][:5]
    if not out["supporting_comment_ids"]:
        out["supporting_comment_ids"] = out["citation_ids"][:2]
    if not out["concerning_comment_ids"]:
        out["concerning_comment_ids"] = out["citation_ids"][2:3] or out["citation_ids"][:1]
    out["discussion_strength_accept"] = _as_float(out.get("discussion_strength_accept"), 0.5, 0.0, 1.0)
    out["discussion_strength_reject"] = _as_float(out.get("discussion_strength_reject"), 0.5, 0.0, 1.0)
    out["score_delta"] = _as_float(out.get("score_delta"), 0.0, -1.75, 1.75)
    out["accept_probability_delta"] = _as_float(out.get("accept_probability_delta"), 0.0, -0.25, 0.25)
    out["confidence"] = _as_float(out.get("confidence"), fallback["confidence"], 0.0, 1.0)
    out["synthesis_markdown"] = _trim(str(out.get("synthesis_markdown") or fallback["synthesis_markdown"]), 1200)
    out["rationale"] = _trim(str(out.get("rationale") or fallback["rationale"]), 1200)
    return out


def synthesize_discussion_with_llm(
    paper: PaperRecord,
    prediction: PredictionBundle,
    comments: list[CommentRecord],
    agent_name: str,
    same_owner_agent_names: set[str],
    discussion_update: dict[str, Any] | None,
    harness_context: dict[str, Any] | None,
    config: dict[str, Any] | None = None,
    *,
    min_comment_quality: float = 0.0,
    min_citations: int = 3,
    force: bool = False,
) -> dict[str, Any]:
    cfg = config or load_config()
    if not agentic_llm_enabled(cfg) or not bool(_agentic_cfg(cfg).get("discussion_synthesis_enabled", True)):
        return validate_discussion_synthesis({}, comments, agent_name, same_owner_agent_names, min_comment_quality, min_citations)
    agentic = _agentic_cfg(cfg)
    max_comments = int(agentic.get("max_comments_for_discussion_prompt", 24))
    summaries = _comment_summaries(comments, agent_name, same_owner_agent_names, limit=max_comments)
    fallback = _fallback_discussion_synthesis(comments, agent_name, same_owner_agent_names, min_comment_quality, min_citations)
    context = _paper_context_for_prompt(paper, prediction)
    prompt = f"""You are the discussion synthesis brain of a Koala Science review agent.

{_koala_agent_background()}

Read the eligible external comments and decide which comments should be cited in the private verdict. You must cite at least {min_citations} comments from distinct external agents. Do not cite the current agent or same-owner agents. Prefer comments that make paper-specific, decision-relevant claims. Use the non-LLM discussion model only as a tool; your synthesis should decide how much the discussion changes the score.

Return ONLY one valid JSON object:
{{
  "citation_ids": ["comment_id_1", "comment_id_2", "comment_id_3"],
  "supporting_comment_ids": ["..."],
  "concerning_comment_ids": ["..."],
  "discussion_strength_accept": 0.0,
  "discussion_strength_reject": 0.0,
  "score_delta": 0.0,
  "accept_probability_delta": 0.0,
  "synthesis_markdown": "short private synthesis of what the discussion established",
  "confidence": 0.0,
  "rationale": "why these comments were selected and how the score should move"
}}

score_delta must be between -1.75 and 1.75. accept_probability_delta must be between -0.25 and 0.25.

Context:
{json.dumps({"paper": context, "eligible_external_comments": summaries, "non_llm_discussion_update": discussion_update or {}, "harness_context": harness_context or {}, "agent_name": agent_name, "same_owner_agent_names": sorted(same_owner_agent_names)}, ensure_ascii=False, sort_keys=True)}
"""
    raw = _call_json_task(
        "discussion",
        paper.paper_id,
        prompt,
        cfg,
        fallback=fallback,
        temperature=float(agentic.get("discussion_temperature", 0.1)),
        force=force,
    )
    return validate_discussion_synthesis(raw, comments, agent_name, same_owner_agent_names, min_comment_quality, min_citations)


def _score_band(score: float) -> str:
    if score < 3.0:
        return "clear reject"
    if score < 5.0:
        return "weak reject"
    if score < 7.0:
        return "weak accept"
    if score < 9.0:
        return "strong accept"
    return "spotlight-quality accept"


def _tool_prior_score(prediction: PredictionBundle, discussion_update: dict[str, Any] | None, harness_context: dict[str, Any] | None) -> float:
    p_final = float((discussion_update or {}).get("p_accept_final", prediction.paper_only.get("p_accept", 0.5)))
    uncertainty = float(prediction.paper_only.get("uncertainty", 0.4))
    q = probability_to_quality_percentile(p_final)
    score = shrink_score_for_uncertainty(percentile_to_koala_score(q), uncertainty)
    h_score = (harness_context or {}).get("calibration", {}).get("harness_score_hint")
    if h_score is not None:
        score = 0.62 * float(score) + 0.38 * float(h_score)
    return float(clamp(score, 0.0, 10.0))


def _validate_verdict_refs(body: str, score: float, citations: list[CommentRecord], agent_name: str, same_owner_agent_names: set[str]) -> None:
    refs = set(re.findall(r"\[\[comment:([^\]]+)\]\]", body or ""))
    expected = {c.comment_id for c in citations}
    if not expected <= refs:
        raise ValueError("verdict body missing required references")
    if len({c.author_agent for c in citations}) < 3:
        raise ValueError("verdict citations must cover at least 3 distinct external agents")
    if any(c.author_agent == agent_name or c.author_agent in same_owner_agent_names for c in citations):
        raise ValueError("verdict contains self or same-owner citation")
    if not 0.0 <= float(score) <= 10.0:
        raise ValueError("score must be in [0, 10]")


def _fallback_verdict_body(score: float, citations: list[CommentRecord], prediction: PredictionBundle, discussion_synthesis: dict[str, Any], harness_context: dict[str, Any] | None) -> str:
    band = _score_band(score)
    refs = " ".join(f"[[comment:{c.comment_id}]]" for c in citations)
    support = _trim("; ".join(prediction.paper_only.get("main_positive_evidence", [])[:2]) or "The paper contains some concrete support for its contribution.", 420)
    risk = _trim("; ".join(prediction.paper_only.get("main_negative_evidence", [])[:2]) or "The main risk is whether the strongest claim is supported under fair comparisons and clear scope limits.", 420)
    synthesis = _trim(discussion_synthesis.get("synthesis_markdown", "The cited discussion provides both support and concerns."), 520)
    feedback = ""
    actions = [str(x) for x in (harness_context or {}).get("feedback_actions", []) if str(x).strip()]
    if actions:
        feedback = f" The most useful remaining check is: {actions[0].rstrip('.')} ."
    return f"""**Verdict: {score:.1f}/10 — {band}**

I am scoring this paper as **{band}** after combining my paper-internal audit with the external discussion.

**Paper-internal case for acceptance.** {support}

**Paper-internal risk.** {risk}

**How the discussion affected my view.** {synthesis} I relied on the following external comments because they make paper-specific, decision-relevant claims: {refs}

**Calibration.** I place the paper near this score because the accept-side evidence and reject-side risk remain in tension. I would move upward if the cited concerns are resolved by direct evidence in the paper; I would move downward if the central comparison, ablation, theoretical link, or scope claim is weaker than the discussion currently assumes.{feedback}

**Final score:** {score:.1f}
"""


def prepare_agentic_verdict(
    paper: PaperRecord,
    prediction: PredictionBundle,
    comments: list[CommentRecord],
    agent_name: str,
    same_owner_agent_names: set[str],
    discussion_update: dict[str, Any] | None,
    discussion_synthesis: dict[str, Any],
    harness_context: dict[str, Any] | None,
    config: dict[str, Any] | None = None,
    *,
    force: bool = False,
) -> VerdictDraft:
    cfg = config or load_config()
    citation_ids = [str(x) for x in discussion_synthesis.get("citation_ids", [])]
    comments_by_id = {c.comment_id: c for c in comments}
    citations = [comments_by_id[cid] for cid in citation_ids if cid in comments_by_id]
    if len({c.author_agent for c in citations}) < 3:
        citations = select_citations(comments, agent_name, same_owner_agent_names, min_citations=3, max_citations=5, min_quality=float(cfg.get("online_policy", {}).get("min_comment_quality_to_cite", 0.0)))
    tool_prior = _tool_prior_score(prediction, discussion_update, harness_context)
    tool_prior = clamp(tool_prior + float(discussion_synthesis.get("score_delta", 0.0)), 0.0, 10.0)
    fallback_score = round(float(tool_prior), 1)
    fallback_body = _fallback_verdict_body(fallback_score, citations, prediction, discussion_synthesis, harness_context)
    if not agentic_llm_enabled(cfg) or not bool(_agentic_cfg(cfg).get("verdict_writer_enabled", True)):
        _validate_verdict_refs(fallback_body, fallback_score, citations, agent_name, same_owner_agent_names)
        return VerdictDraft(score=fallback_score, verdict_markdown=fallback_body, citation_ids=[c.comment_id for c in citations])

    agentic = _agentic_cfg(cfg)
    citation_payload = []
    for c in citations:
        citation_payload.append(
            {
                "comment_id": c.comment_id,
                "author_agent": c.author_agent,
                "content_excerpt": _trim(c.content_markdown, 1100),
                "quality_score": c.quality_score,
            }
        )
    prompt = f"""You are the final verdict writer for a Koala Science peer-review agent.

{_koala_agent_background()}

{_domain_lens_for_prompt(paper.domains)}

Write a private verdict for a paper in the deliberating phase. The verdict must cite every selected external comment exactly using [[comment:COMMENT_ID]] references. Do not cite yourself or same-owner agents. Use only paper evidence and the current Koala discussion; do not use official reviews, decisions, acceptance status, citation counts, awards, social media, news, or later impact.

The traditional statistical prior, paper-evidence extractor, and discussion model are tools. You may override them, but explain the scientific reason. Keep scores calibrated to the Koala bands: 0-2.99 clear reject, 3-4.99 weak reject, 5-6.99 weak accept, 7-8.99 strong accept, 9-10 spotlight-quality.

Return ONLY one valid JSON object:
{{
  "score": 0.0,
  "verdict_markdown": "markdown verdict containing every required [[comment:...]] reference",
  "score_rationale": "...",
  "confidence": 0.0
}}

Required citation IDs: {json.dumps([c.comment_id for c in citations], ensure_ascii=False)}
Tool prior score after discussion: {tool_prior:.2f}
Paper/tool context:
{json.dumps({"paper": _paper_context_for_prompt(paper, prediction), "harness_context": harness_context or {}, "non_llm_discussion_update": discussion_update or {}, "llm_discussion_synthesis": discussion_synthesis, "selected_external_comments": citation_payload}, ensure_ascii=False, sort_keys=True)}
"""
    raw = _call_json_task(
        "verdict",
        paper.paper_id,
        prompt,
        cfg,
        fallback={"score": fallback_score, "verdict_markdown": fallback_body, "score_rationale": "Fallback verdict.", "confidence": 0.35, "fallback": True},
        temperature=float(agentic.get("verdict_temperature", 0.15)),
        force=force,
    )
    llm_score = _as_float(raw.get("score"), fallback_score, 0.0, 10.0)
    conf = _as_float(raw.get("confidence"), 0.5, 0.0, 1.0)
    max_dev = float(agentic.get("max_score_deviation_from_tool_prior", 2.2))
    # LLM is the lead judge, but keep live submissions inside a configurable
    # sanity envelope unless the prompt/config explicitly changes it.
    llm_score = float(clamp(llm_score, tool_prior - max_dev, tool_prior + max_dev))
    weight = float(agentic.get("final_score_llm_weight", 0.78))
    if bool(raw.get("fallback", False)):
        weight = 0.0
    elif conf < 0.35:
        weight = min(weight, 0.45)
    score = round(float(clamp(weight * llm_score + (1.0 - weight) * tool_prior, 0.0, 10.0)), 1)
    body = str(raw.get("verdict_markdown") or fallback_body).strip() + "\n"
    for c in citations:
        token = f"[[comment:{c.comment_id}]]"
        if token not in body:
            body = body.rstrip() + f"\n\nAdditional cited discussion reference: {token}\n"
    # If score changed after blending, update the first visible verdict line and
    # final score without rewriting the whole LLM body.
    body = re.sub(r"\*\*Verdict:\s*[-0-9.]+/10\s*—\s*[^*]+\*\*", f"**Verdict: {score:.1f}/10 — {_score_band(score)}**", body, count=1)
    body = re.sub(r"\*\*Final score:\*\*\s*[-0-9.]+", f"**Final score:** {score:.1f}", body)
    if "**Final score:**" not in body:
        body = body.rstrip() + f"\n\n**Final score:** {score:.1f}\n"
    try:
        _validate_verdict_refs(body, score, citations, agent_name, same_owner_agent_names)
    except Exception:
        body = _fallback_verdict_body(score, citations, prediction, discussion_synthesis, harness_context)
        _validate_verdict_refs(body, score, citations, agent_name, same_owner_agent_names)
    return VerdictDraft(score=score, verdict_markdown=body, citation_ids=[c.comment_id for c in citations])

def _candidate_context_for_pool(row: dict[str, Any]) -> dict[str, Any]:
    paper: PaperRecord = row["paper"]
    prediction: PredictionBundle = row["prediction"]
    comments: list[CommentRecord] = row.get("comments", []) or []
    return {
        "paper_id": paper.paper_id,
        "title": paper.title,
        "abstract": _trim(paper.abstract or "", 1400),
        "domains": paper.domains,
        "status": paper.status,
        "comment_count": paper.comment_count,
        "participant_count": paper.participant_count,
        "base_utility": float(row.get("utility", 0.0)),
        "non_llm_prior": {
            "p_accept": prediction.paper_only.get("p_accept"),
            "recommended_score_range": prediction.paper_only.get("recommended_score_range"),
            "uncertainty": prediction.paper_only.get("uncertainty"),
            "positive_evidence": prediction.paper_only.get("main_positive_evidence", [])[:2],
            "negative_evidence": prediction.paper_only.get("main_negative_evidence", [])[:2],
        },
        "component_diagnostics": prediction.pseudo_review_panel.get("reviewer_component_summary", [])[:5] if isinstance(prediction.pseudo_review_panel, dict) else [],
        "external_discussion_excerpt": _comment_summaries(comments, "", set(), limit=4),
    }


def run_llm_candidate_pool_planner(
    rows: list[dict[str, Any]],
    agent_name: str,
    config: dict[str, Any] | None = None,
    *,
    force: bool = False,
) -> dict[str, dict[str, Any]]:
    """Global LLM planner over the candidate pool.

    Unlike per-paper triage, this call lets the LLM compare papers and decide
    where the agent should spend scarce karma.  Traditional utilities and model
    predictions are exposed as tools, not as the final decision maker.
    """
    cfg = config or load_config()
    if not rows:
        return {}
    fallback = {
        "global_strategy": "Fallback to per-paper triage because the pool planner was unavailable.",
        "paper_plans": [],
    }
    if not agentic_llm_enabled(cfg):
        return {row["paper"].paper_id: validate_triage({"fallback": True}, row["paper"], row["prediction"], float(row.get("utility", 0.0))) for row in rows}
    agentic = _agentic_cfg(cfg)
    contexts = [_candidate_context_for_pool(row) for row in rows]
    key = content_hash("|".join(str(ctx["paper_id"]) for ctx in contexts))[:16]
    prompt = f"""You are the autonomous planning brain of a Koala Science peer-review agent.

Compare the candidate papers and decide where this agent should spend its next comment actions. You may use traditional utilities, calibrated priors, component diagnostics, and existing discussion as tools. The final selection is yours. Prefer fewer, deeper, paper-specific comments over broad generic coverage. Skip papers where the best available contribution would be generic or duplicative.

Return ONLY one valid JSON object:
{{
  "global_strategy": "brief private strategy",
  "paper_plans": [
    {{
      "paper_id": "...",
      "should_comment": true,
      "comment_worthiness": 0.0,
      "specific_evidence_quality": 0.0,
      "likely_discussion_value": 0.0,
      "risk_of_generic_comment": 0.0,
      "utility_multiplier": 1.0,
      "utility_bonus": 0.0,
      "use_traditional_prior_weight": 0.0,
      "comment_angle": "...",
      "claim_to_audit": "...",
      "evidence_to_use": ["..."],
      "concerns_to_raise": ["..."],
      "tool_plan": [{{"tool": "non_llm_prior|component_diagnostics|discussion_context|paper_evidence", "decision": "use|downweight|ignore", "weight": 0.0, "why": "..."}}],
      "confidence": 0.0,
      "rationale": "..."
    }}
  ]
}}

Use scales 0 to 1 except utility_multiplier and utility_bonus. Keep utility_multiplier between 0.35 and 1.75, utility_bonus between -3 and 3. Include every candidate paper_id in paper_plans, even if should_comment=false.

Candidate pool:
{json.dumps({"agent_name": agent_name, "candidates": contexts}, ensure_ascii=False, sort_keys=True)}
"""
    raw = _call_json_task(
        "candidate_pool",
        key,
        prompt,
        cfg,
        fallback=fallback,
        temperature=float(agentic.get("triage_temperature", 0.1)),
        force=force,
    )
    plans = raw.get("paper_plans") if isinstance(raw.get("paper_plans"), list) else []
    by_id = {str(item.get("paper_id")): item for item in plans if isinstance(item, dict) and item.get("paper_id")}
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        paper = row["paper"]
        plan = by_id.get(paper.paper_id)
        if plan is None:
            plan = {"fallback": True, "rationale": "Pool planner omitted this paper; using fallback triage."}
        validated = validate_triage(plan, paper, row["prediction"], float(row.get("utility", 0.0)))
        validated["pool_planner_strategy"] = _trim(str(raw.get("global_strategy") or ""), 800)
        validated["pool_planner_fallback"] = bool(raw.get("fallback", False))
        out[paper.paper_id] = validated
    return out
