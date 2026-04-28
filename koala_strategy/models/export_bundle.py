from __future__ import annotations

from typing import Any

from koala_strategy.config import load_config, resolve_path
from koala_strategy.llm.pseudo_review_panel import aggregate_pseudo_reviews, run_pseudo_review_panel
from koala_strategy.models.predict_paper_only import load_model_artifacts, predict_paper_only
from koala_strategy.models.score_mapping import probability_to_quality_percentile, percentile_to_koala_score, score_range, shrink_score_for_uncertainty
from koala_strategy.models.fulltext_evidence_model import build_fulltext_feature_frame, parsed_payload_for_paper
from koala_strategy.paper.table_evidence import best_table_evidence
from koala_strategy.paper.paper_features import evidence_snippets, parsed_from_paper_record
from koala_strategy.paper.reviewer_components import component_signal_summary
from koala_strategy.schemas import ParsedPaperText
from koala_strategy.schemas import PaperRecord, PredictionBundle
from koala_strategy.utils import dump_json, ensure_dir, normalize_domain


def _main_evidence(paper: PaperRecord) -> tuple[list[str], list[str]]:
    payload = parsed_payload_for_paper(paper.paper_id)
    if payload:
        table_bits = []
        for item in best_table_evidence(payload.get("table_evidence") or [], limit=2):
            context = " ".join(str(item.get("caption_or_context", "")).split())[:280]
            if " Method " in context:
                context = context.split(" Method ", 1)[0]
            table_bits.append(
                f"Table {item.get('table_id')} contains comparison evidence with {item.get('num_numeric_values', 0)} numeric values"
                + (f" and metrics {', '.join(item.get('metrics') or [])}" if item.get("metrics") else "")
                + f": {context}"
            )
        if table_bits:
            negative = ["The table evidence is strong only if the comparison set, evaluation protocol, and metric choices are accepted as fair."]
            return table_bits, negative
    parsed = parsed_from_paper_record(paper)
    snippets = evidence_snippets(parsed)
    positive = snippets.get("positive") or ["The paper states a concrete contribution and reports supporting evidence."]
    negative = snippets.get("negative") or ["The main decision risk is whether the strongest claim is fully supported by the evidence."]
    return positive[:3], negative[:3]


def generate_prediction_bundle(
    paper: PaperRecord,
    artifacts: dict[str, Any] | None = None,
    config: dict[str, Any] | None = None,
    save: bool = True,
) -> PredictionBundle:
    cfg = config or load_config()
    artifacts = artifacts or load_model_artifacts(cfg)
    pred = predict_paper_only([paper], artifacts)
    p_accept = float(pred["p_accept"][0])
    p_a = float(pred["p_model_a"][0])
    p_b = float(pred["p_model_b"][0])
    uncertainty = float(pred["uncertainty"][0])
    fulltext_summary: dict[str, Any] = {"available": False}
    if cfg.get("models", {}).get("use_fulltext_evidence", True) and (
        "fulltext_text_evidence_model" in artifacts or "fulltext_evidence_model" in artifacts
    ):
        payload = parsed_payload_for_paper(paper.paper_id)
        if payload:
            try:
                ft_df, ft_records = build_fulltext_feature_frame([paper], artifacts)
                if len(ft_df) == 1:
                    text_model = artifacts.get("fulltext_text_evidence_model")
                    if text_model is not None:
                        ft_model = text_model
                        p_fulltext = float(ft_model.predict_proba([payload], ft_df)[0])
                    else:
                        ft_model = artifacts["fulltext_evidence_model"]
                        p_fulltext = float(ft_model.predict_proba(ft_df)[0])
                    model_alpha = float(getattr(ft_model, "blend_alpha", 0.9))
                    alpha_cap = float(cfg.get("models", {}).get("fulltext_blend_alpha_max", 0.6))
                    alpha = max(0.0, min(model_alpha, alpha_cap))
                    if bool(cfg.get("models", {}).get("fulltext_extreme_shrink", True)):
                        p_fulltext_for_blend = 0.5 + 0.85 * (p_fulltext - 0.5)
                    else:
                        p_fulltext_for_blend = p_fulltext
                    p_accept = (1.0 - alpha) * p_accept + alpha * p_fulltext_for_blend
                    uncertainty = max(0.05, min(1.0, 0.7 * uncertainty + 0.3 * abs(p_fulltext_for_blend - float(pred["p_accept"][0]))))
                    fulltext_summary = {
                        "available": True,
                        "model_name": str(getattr(ft_model, "name", "numeric_fulltext_evidence")),
                        "p_fulltext": p_fulltext,
                        "p_fulltext_for_blend": p_fulltext_for_blend,
                        "blend_alpha_model": model_alpha,
                        "blend_alpha_used": alpha,
                        "leakage_mitigation": "fulltext contribution capped by config and extreme full-text predictions shrunk toward 0.5",
                        "parsed_tokens": len((payload.get("full_text") or "").split()),
                        "num_table_evidence": len(payload.get("table_evidence") or []),
                        "best_table_evidence": [
                            {**item, "caption_or_context": " ".join(str(item.get("caption_or_context", "")).split())[:500]}
                            for item in best_table_evidence(payload.get("table_evidence") or [], limit=3)
                        ],
                        "parser_warnings": payload.get("parser_warnings", []),
                    }
            except Exception as exc:  # noqa: BLE001
                fulltext_summary = {"available": False, "error": str(exc)[:300]}
    domain = normalize_domain(paper.domains[0]) if paper.domains else None
    stacker = artifacts["stacker"]
    pool = stacker.domain_distributions.get(domain, stacker.train_distribution) if hasattr(stacker, "domain_distributions") else None
    percentile = probability_to_quality_percentile(p_accept, domain=domain, current_pool_distribution=pool)
    raw_score = percentile_to_koala_score(percentile)
    score = shrink_score_for_uncertainty(raw_score, uncertainty)
    lo, hi = score_range(score, uncertainty)
    payload_for_components = parsed_payload_for_paper(paper.paper_id)
    parsed = ParsedPaperText.model_validate(payload_for_components) if payload_for_components else parsed_from_paper_record(paper)
    pseudo_reviews = run_pseudo_review_panel(parsed)
    panel = aggregate_pseudo_reviews(pseudo_reviews)
    panel["p_accept_from_panel"] = p_b
    reviewer_components = component_signal_summary(parsed)
    panel["reviewer_component_summary"] = reviewer_components
    positive, negative = _main_evidence(paper)
    strategy = "balanced_borderline"
    if score >= 6.5:
        strategy = "weak_accept_if_no_new_fatal_flaw"
    elif score <= 4.0:
        strategy = "reject_unless_discussion_resolves_core_risk"
    bundle = PredictionBundle(
        paper_id=paper.paper_id,
        domain=domain,
        model_version=cfg.get("models", {}).get("version", "iclr26_v1"),
        paper_only={
            "p_accept": p_accept,
            "p_model_a": p_a,
            "p_model_b": p_b,
            "fulltext_evidence": fulltext_summary,
            "quality_percentile": percentile,
            "uncertainty": uncertainty,
            "recommended_score_range": [lo, hi],
            "main_positive_evidence": positive,
            "main_negative_evidence": negative,
            "do_not_score_above": min(9.5, hi + 0.6),
        },
        pseudo_review_panel=panel,
        discussion_update={"available": False},
        agent_instruction={
            "comment_strategy": strategy,
            "verdict_strategy": strategy,
            "reviewer_feedback_actions": [row["assessment"] for row in reviewer_components[:3]],
        },
    )
    if save:
        out_dir = ensure_dir(resolve_path(cfg, "koala_cache_dir") / "predictions")
        dump_json(out_dir / f"{paper.paper_id}.json", bundle.model_dump(mode="json"))
    return bundle
