from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from koala_strategy.config import load_config, project_root, resolve_path
from koala_strategy.llm.json_guard import extract_json_object
from koala_strategy.llm.providers import get_text_provider
from koala_strategy.paper.parsed_payload import parsed_payload_for_paper
from koala_strategy.paper.reviewer_components import component_signal_summary
from koala_strategy.paper.table_evidence import best_table_evidence
from koala_strategy.paper.text_sanitizer import sanitize_model_text, sanitized_sections_text
from koala_strategy.schemas import PaperRecord, PredictionBundle
from koala_strategy.utils import clamp, content_hash, dump_json, ensure_dir


PROMPT_VERSION = "llm_review_judge_v2"
REQUIRED_LLM_JUDGE_FIELDS = (
    "accept_probability",
    "verdict_score",
    "confidence",
    "strongest_accept_signal",
    "strongest_reject_signal",
    "short_rationale",
)


LEAKY_WORDS = [
    "accepted",
    "published",
    "camera-ready",
    "under review",
    "submitted",
    "conference paper",
    "iclr 2026",
    "openreview",
    "paper decision",
    "acknowledg",
    "author contribution",
    "corresponding author",
]


def _clean_line(line: str) -> str:
    return sanitize_model_text(line)


def _trim(text: str, max_chars: int) -> str:
    text = sanitize_model_text(text)
    if len(text) <= max_chars:
        return text
    return text[: int(max_chars * 0.75)] + "\n...\n" + text[-int(max_chars * 0.25) :]


def _bool_from_env(name: str) -> bool:
    value = str(os.getenv(name, "0")).strip().lower()
    return value in {"1", "true", "yes", "on", "enabled"}


def _external_review_features_enabled() -> bool:
    return _bool_from_env("LLM_JUDGE_USE_EXTERNAL_REVIEW_FEATURES")


def _review_score_summary(paper: PaperRecord) -> dict[str, Any]:
    meta = paper.metadata if isinstance(paper.metadata, dict) else {}
    if not isinstance(meta, dict):
        return {}
    summary: dict[str, Any] = {}
    review_scores = meta.get("review_scores")
    if isinstance(review_scores, dict) and review_scores:
        summary["review_scores"] = {
            "soundness": safe_mean(review_scores.get("soundness")),
            "confidence_mean": safe_mean(review_scores.get("confidence_mean")),
            "presentation": safe_mean(review_scores.get("presentation")),
            "rating_mean": safe_mean(review_scores.get("rating_mean")),
            "contribution": safe_mean(review_scores.get("contribution")),
        }
    official_count = meta.get("official_review_count")
    if official_count is not None:
        try:
            summary["official_review_count"] = int(official_count)
        except (TypeError, ValueError):
            pass
    suggested_score = meta.get("suggested_verdict_score")
    if suggested_score is not None:
        try:
            summary["suggested_verdict_score"] = float(suggested_score)
        except (TypeError, ValueError):
            pass
    source_status = meta.get("source_status")
    if source_status:
        summary["source_status"] = str(source_status)
    return summary


def safe_mean(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, (list, tuple)):
        values = [float(v) for v in value if isinstance(v, (int, float))]
        if not values:
            return None
        return float(sum(values) / len(values))
    return None


def llm_evidence_payload(paper: PaperRecord, prediction: PredictionBundle) -> dict[str, Any]:
    payload = parsed_payload_for_paper(paper.paper_id) or {}
    tables = []
    for item in best_table_evidence(payload.get("table_evidence") or [], limit=6):
        tables.append(
            {
                "table_id": item.get("table_id"),
                "metrics": item.get("metrics", []),
                "num_numeric_values": item.get("num_numeric_values", 0),
                "context": _trim(str(item.get("caption_or_context", "")), 1200),
            }
        )
    sections = payload.get("sections") or {}
    selected_sections_text = sanitized_sections_text(sections, max_chars_per_section=3200)
    if _external_review_features_enabled():
        review_features = _review_score_summary(paper)
        if review_features:
            payload = {**payload, "official_review_features": review_features}
    return {
        "paper_id": paper.paper_id,
        "title": paper.title,
        "abstract": _trim(paper.abstract or "", 2400),
        "domains": paper.domains,
        "non_llm_prior": {
            "p_accept": prediction.paper_only.get("p_accept"),
            "recommended_score_range": prediction.paper_only.get("recommended_score_range"),
            "uncertainty": prediction.paper_only.get("uncertainty"),
            "positive_evidence": prediction.paper_only.get("main_positive_evidence", [])[:3],
            "negative_evidence": prediction.paper_only.get("main_negative_evidence", [])[:3],
        },
        "reviewer_component_diagnostics": component_signal_summary_from_payload(paper, prediction),
        "tables": tables,
        "selected_sections": _trim(selected_sections_text, 18000),
        "sanitized_full_text_excerpt": _trim(str(payload.get("full_text") or ""), 22000),
    }


DOMAIN_EXPERT_GUIDANCE = {
    "Theory": "Theory/optimization lens: reward clean problem setup, nontrivial guarantees, correct assumptions, and proof-evidence fit; penalize narrow toy settings, missing baselines only when empirical claims are central, and gaps between theorem conditions and claims.",
    "Optimization": "Theory/optimization lens: reward convergence/sample-complexity clarity and assumption transparency; penalize unclear constants, unrealistic regimes, or missing comparison to standard algorithms.",
    "NLP": "NLP/LLM lens: reward strong baselines, realistic datasets, ablations, robustness, and reproducibility; penalize benchmark cherry-picking, prompt sensitivity, weak contamination controls, and claims beyond measured tasks.",
    "LLM-Alignment": "LLM/alignment lens: reward safety-relevant evaluation, human/preference protocol clarity, ablations, and failure analysis; penalize weak judge validity, unclear data provenance, and overbroad alignment claims.",
    "Computer-Vision": "Vision lens: reward strong baselines, diverse datasets, ablations, robustness/shift tests, and compute clarity; penalize narrow benchmark gains, missing comparison to current methods, or unclear training recipes.",
    "Generative-Models": "Generative modeling lens: reward likelihood/sample-quality tradeoff clarity, compute/data disclosure, ablations, and evaluation reliability; penalize cherry-picked qualitative evidence and weak safety/robustness checks.",
    "Robotics": "Robotics lens: reward real-world or high-fidelity evaluation, clear task diversity, reproducibility, and failure modes; penalize tiny task suites, simulator-only overclaims, and missing ablations on perception/control components.",
    "Reinforcement-Learning": "RL lens: reward seed statistics, robust baselines, environment diversity, ablations, and stability analysis; penalize single-seed gains, reward hacking ambiguity, or weak compute/sample-efficiency reporting.",
    "Graph-Learning": "Graph lens: reward strong split protocols, heterophily/scale coverage, ablations, and leakage controls; penalize benchmark reuse without new insight or weak comparison to simple baselines.",
    "Trustworthy-ML": "Trustworthy ML lens: reward threat model clarity, rigorous evaluation, calibration/robustness/fairness evidence, and limitations; penalize vague safety claims or evaluation that does not match the threat model.",
    "Healthcare-Science-Applications": "Science/healthcare lens: reward external validity, clinically/scientifically meaningful metrics, uncertainty, privacy/ethics, and failure analysis; penalize weak cohorts, leakage, and overstated deployment claims.",
    "Speech-Audio": "Speech/audio lens: reward dataset diversity, noise/condition robustness, strong baselines, and reproducibility; penalize narrow acoustic conditions or weak cross-domain validation.",
}


def _prompt_profile(config: dict[str, Any]) -> str:
    return str(config.get("models", {}).get("llm_judge_prompt_profile") or "domain_calibrated_v2")


def _domain_expert_guidance(domains: list[str] | None) -> list[str]:
    rows: list[str] = []
    for domain in domains or []:
        if domain in DOMAIN_EXPERT_GUIDANCE:
            rows.append(DOMAIN_EXPERT_GUIDANCE[domain])
        if len(rows) >= 3:
            break
    if not rows:
        rows.append("General ML lens: reward clear claim-evidence alignment, strong comparisons, ablations, robustness, reproducibility, novelty, and honest limitations; penalize overclaiming and unsupported scope.")
    return rows


def _competition_context(profile: str) -> str:
    strictness = "standard"
    if "skeptical" in profile:
        strictness = "strict"
    contrastive = ""
    if "contrastive" in profile:
        contrastive = """
Contrastive calibration rule:
- First decide the stronger side: accept case or reject case. The final probability must reflect that lean.
- If the reject case is slightly stronger, use 0.40-0.49, not a polite 0.55.
- If the accept case is only slightly stronger, use 0.52-0.58.
- Use 0.58-0.66 only when the accept case has at least two independent strengths and the strongest reject signal is limited.
- Do not cluster all decent papers around 0.56-0.60. Reserve that band for genuinely weak-accept papers.
- A paper can be technically sophisticated and still below 0.50 if the contribution, assumptions, or validation do not meet the acceptance bar.
"""
    return f"""Koala Science context:
- Multiple agents review the same active papers. A useful agent spends scarce karma only where its evidence-based judgment or comment can improve the final discussion.
- Karma is the agent's limited budget: first comments and follow-ups cost karma, so a high score/probability should require decision-relevant evidence, not just a polished abstract.
- The model is being used as an internal reviewer, not as an author advocate. Your job is calibrated peer-review judgment for a selective ML venue.
- The hidden task evaluates whether your probability/ranking correlates with eventual accept vs reject labels. Good calibration matters as much as enthusiasm.
- Use only public paper evidence in the payload. Ignore any status, official review, citation, fame, or post-decision signals if they appear.

Calibration anchors:
- 0.50 means genuinely borderline. It is not a default positive score.
- 0.35-0.45: plausible but likely below acceptance bar or evidence has major gaps.
- 0.45-0.55: borderline/mixed; use this for many solid-but-not-decisive papers.
- 0.55-0.65: weak-to-moderate accept only when evidence clearly beats main risks.
- 0.65-0.75: clear accept; needs strong independent evidence across technical soundness and validation.
- >0.75: rare, reserved for unusually strong papers with low decision risk.
- <0.30: clear reject or severe flaw; also rare from paper evidence alone.
- For a selective venue, a well-written paper with reasonable experiments can still be 0.45-0.55 if novelty, baselines, or claim support are only moderate.
- Move away from the non-LLM prior cautiously: about 0.05 for mild evidence, 0.10 for solid evidence, 0.20 only for decisive evidence.

{contrastive}
Strictness mode: {strictness}."""


def _rubric_context(profile: str, domains: list[str] | None) -> str:
    domain_guidance = "\n".join(f"- {row}" for row in _domain_expert_guidance(domains))
    if "general" in profile:
        domain_guidance = "- Use the general ML reviewer lens; avoid overfitting the judgment to a single subfield convention."
    return f"""Reviewer axes:
1. empirical validation and comparison completeness
2. clarity, presentation, and reproducibility
3. robustness, generalization, scalability, and compute
4. technical soundness and claim support
5. novelty, contribution, and prior-work positioning

Internal expert lenses to consult, while keeping the final judgment generalizable:
{domain_guidance}

Decision discipline:
- Separate paper quality from acceptance probability. A technically interesting paper can remain borderline if evidence is narrow.
- Explicitly look for the strongest reject signal before assigning p_accept above 0.60.
- If evidence is mostly abstract-level or table parser context is incomplete, lower confidence and avoid extreme probabilities.
- Do not reward verbosity, benchmark count, or mathematical density unless it supports the central claim."""


def component_signal_summary_from_payload(paper: PaperRecord, prediction: PredictionBundle) -> list[dict[str, Any]]:
    try:
        # The prediction bundle already stores this when generated after V4.
        rows = prediction.pseudo_review_panel.get("reviewer_component_summary", [])
        if rows:
            return rows
    except Exception:  # noqa: BLE001
        pass
    payload = parsed_payload_for_paper(paper.paper_id)
    if not payload:
        return []
    from koala_strategy.schemas import ParsedPaperText

    return component_signal_summary(ParsedPaperText.model_validate(payload))


def build_llm_judge_prompt(paper: PaperRecord, prediction: PredictionBundle, config: dict[str, Any] | None = None) -> str:
    payload = llm_evidence_payload(paper, prediction)
    cfg = config or load_config()
    profile = _prompt_profile(cfg)
    return f"""You are the LLM brain inside a Koala Science ICML 2026 review agent.

{_competition_context(profile)}

{_rubric_context(profile, paper.domains)}

Use the non-LLM prior as a calibrated statistical prior, not as ground truth.
You may override it, but only after comparing concrete accept evidence against
concrete reject evidence. Prefer a calibrated probability over a flattering
review tone.

Return ONLY one valid JSON object with this exact shape:
{{
  "accept_probability": 0.0,
  "verdict_score": 0.0,
  "confidence": 0.0,
  "axes": {{
    "empirical_validation": {{"score": 0.0, "risk": 0.0, "rationale": "..."}},
    "clarity_reproducibility": {{"score": 0.0, "risk": 0.0, "rationale": "..."}},
    "practical_scope": {{"score": 0.0, "risk": 0.0, "rationale": "..."}},
    "technical_soundness": {{"score": 0.0, "risk": 0.0, "rationale": "..."}},
    "novelty_positioning": {{"score": 0.0, "risk": 0.0, "rationale": "..."}}
  }},
  "strongest_accept_signal": "...",
  "strongest_reject_signal": "...",
  "feedback_actions": ["..."],
  "short_rationale": "..."
}}

Scales:
- accept_probability is 0 to 1.
- verdict_score is 0 to 10.
- confidence is 0 to 1.
- axis score is 0 to 10 where higher is stronger.
- risk is 0 to 1 where higher is more concerning.
- Verdict score guide: 4.0=clear reject, 5.0=borderline reject, 6.0=weak accept, 7.0=clear accept, 8.0=strong accept, 9+ exceptional.
- Keep confidence below 0.65 when the PDF evidence excerpt is incomplete, the key baseline fairness is unclear, or the main claim depends on assumptions not fully checked.

Paper evidence JSON:
{json.dumps(payload, ensure_ascii=False, sort_keys=True)}
"""


def _cache_path(paper_id: str, model: str, prompt: str, config: dict[str, Any]) -> Path:
    cache_root = Path(config.get("models", {}).get("llm_judge_cache_dir") or project_root() / "data" / "llm_judge_cache")
    safe_model = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in model)
    key = content_hash(PROMPT_VERSION + "\n" + _prompt_profile(config) + "\n" + prompt)[:16]
    return ensure_dir(cache_root / safe_model) / f"{paper_id}_{key}.json"


def _raw_response_path(cache_path: Path) -> Path:
    return cache_path.with_suffix(".raw.txt")


def _fallback(prediction: PredictionBundle, error: str | None = None) -> dict[str, Any]:
    p = float(prediction.paper_only.get("p_accept", 0.5))
    lo, hi = prediction.paper_only.get("recommended_score_range", [4.5, 5.5])
    return {
        "accept_probability": p,
        "verdict_score": float(lo + hi) / 2.0,
        "confidence": max(0.1, min(0.9, 1.0 - float(prediction.paper_only.get("uncertainty", 0.5)))),
        "axes": {},
        "strongest_accept_signal": (prediction.paper_only.get("main_positive_evidence") or ["No strong accept signal extracted."])[0],
        "strongest_reject_signal": (prediction.paper_only.get("main_negative_evidence") or ["No strong reject signal extracted."])[0],
        "feedback_actions": ["Use the non-LLM evidence prior; LLM judge was unavailable."],
        "short_rationale": f"Fallback to calibrated prior. {error or ''}".strip(),
        "fallback": True,
    }


def validate_llm_judge(data: dict[str, Any], prediction: PredictionBundle) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError("LLM judge output must be a JSON object.")
    missing = [field for field in REQUIRED_LLM_JUDGE_FIELDS if field not in data]
    if missing:
        raise ValueError(f"LLM judge output missing required fields: {', '.join(missing)}")
    fallback = _fallback(prediction)
    out = {**fallback, **data, "fallback": False}
    out["accept_probability"] = float(clamp(float(out.get("accept_probability", fallback["accept_probability"])), 0.0, 1.0))
    out["verdict_score"] = float(clamp(float(out.get("verdict_score", fallback["verdict_score"])), 0.0, 10.0))
    out["confidence"] = float(clamp(float(out.get("confidence", fallback["confidence"])), 0.0, 1.0))
    axes = out.get("axes") if isinstance(out.get("axes"), dict) else {}
    cleaned_axes: dict[str, Any] = {}
    for key in ["empirical_validation", "clarity_reproducibility", "practical_scope", "technical_soundness", "novelty_positioning"]:
        row = axes.get(key, {}) if isinstance(axes, dict) else {}
        if not isinstance(row, dict):
            row = {}
        cleaned_axes[key] = {
            "score": float(clamp(float(row.get("score", 5.0)), 0.0, 10.0)),
            "risk": float(clamp(float(row.get("risk", 0.5)), 0.0, 1.0)),
            "rationale": str(row.get("rationale", ""))[:500],
        }
    out["axes"] = cleaned_axes
    out["strongest_accept_signal"] = str(out.get("strongest_accept_signal", fallback["strongest_accept_signal"]))[:800]
    out["strongest_reject_signal"] = str(out.get("strongest_reject_signal", fallback["strongest_reject_signal"]))[:800]
    actions = out.get("feedback_actions", [])
    if not isinstance(actions, list):
        actions = [str(actions)]
    out["feedback_actions"] = [str(x)[:300] for x in actions if str(x).strip()][:5] or fallback["feedback_actions"]
    out["short_rationale"] = str(out.get("short_rationale", fallback["short_rationale"]))[:1000]
    return out


def run_llm_review_judge(
    paper: PaperRecord,
    prediction: PredictionBundle,
    config: dict[str, Any] | None = None,
    force: bool = False,
) -> dict[str, Any]:
    cfg = config or load_config()
    model = str(cfg.get("models", {}).get("codex_model", "gpt-5.4-mini"))
    prompt = build_llm_judge_prompt(paper, prediction, cfg)
    path = _cache_path(paper.paper_id, model, prompt, cfg)
    if path.exists() and not force:
        return json.loads(path.read_text(encoding="utf-8"))
    raw_path = _raw_response_path(path)
    provider = get_text_provider(cfg)
    try:
        raw = provider.generate(prompt, model=model, temperature=0.0)
        raw_path.write_text(raw, encoding="utf-8")
        data = validate_llm_judge(extract_json_object(raw), prediction)
        data["model"] = model
        data["prompt_version"] = PROMPT_VERSION
        data["prompt_profile"] = _prompt_profile(cfg)
        data["cache_path"] = str(path)
        data["raw_response_path"] = str(raw_path)
        data["raw_response_hash"] = content_hash(raw)
    except Exception as exc:  # noqa: BLE001
        data = _fallback(prediction, error=str(exc)[:300])
        data["model"] = model
        data["prompt_version"] = PROMPT_VERSION
        data["prompt_profile"] = _prompt_profile(cfg)
        data["cache_path"] = str(path)
        if raw_path.exists():
            data["raw_response_path"] = str(raw_path)
            data["raw_response_hash"] = content_hash(raw_path.read_text(encoding="utf-8"))
    dump_json(path, data)
    return data


def llm_probability_for_blend(judge: dict[str, Any]) -> float:
    p = float(judge.get("accept_probability", 0.5))
    score = float(judge.get("verdict_score", 5.0)) / 10.0
    confidence = float(clamp(float(judge.get("confidence", 0.5)), 0.0, 1.0))
    # Let the direct probability dominate, but anchor extreme scores slightly.
    return float(clamp((0.75 + 0.15 * confidence) * p + (0.25 - 0.15 * confidence) * score, 0.0, 1.0))


def gated_llm_blend(base_p: float, judge: dict[str, Any]) -> dict[str, float]:
    llm_p = llm_probability_for_blend(judge)
    confidence = float(clamp(float(judge.get("confidence", 0.5)), 0.0, 1.0))
    disagreement = abs(float(llm_p) - float(base_p))
    profile = str(judge.get("prompt_profile", ""))
    if "contrastive" in profile and 0.35 <= float(base_p) <= 0.65 and confidence >= 0.45:
        # The contrastive prompt is intended for hard boundary papers where the
        # base model is least informative. Use it as the lead signal there, but
        # still leave some statistical prior mass for calibration stability.
        alpha = 0.75 if confidence >= 0.58 or disagreement >= 0.10 else 0.65
    else:
        alpha = 0.40 if confidence >= 0.65 and disagreement >= 0.12 else 0.10
    return {
        "p_base": float(base_p),
        "p_llm": float(llm_p),
        "llm_confidence": confidence,
        "llm_disagreement": float(disagreement),
        "blend_alpha": alpha,
        "p_accept_llm_blend": float(clamp((1.0 - alpha) * float(base_p) + alpha * float(llm_p), 0.0, 1.0)),
    }
