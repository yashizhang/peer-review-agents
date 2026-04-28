# Koala Review Director V2 Plan

## 0. Pivot

We no longer run three independent Koala platform agents. The production strategy is one public agent:

```text
review_director
```

The review director internally calls subagent personas before acting:

```text
evidence_mapper      -> full-text/table/section evidence extraction
rigor_auditor        -> baselines, ablations, metrics, variance, reproducibility
novelty_scout        -> novelty, prior work, overclaiming
calibration_chair    -> calibrated p_accept and 0-10 score
```

Only `review_director` posts comments and verdicts. The subagents are internal reasoning modules, not separate Koala identities.

## 1. New Prediction Stack

The V1 predictor used title/abstract/metadata plus heuristic pseudo-review features. V2 adds:

```text
PDF full text
  -> sanitized text parser
  -> table evidence parser
  -> stronger pseudo-review / judge features
  -> fulltext evidence head
  -> calibrated blend with V1 prior
```

Important leakage guard: ICLR PDFs can include author names and venue/status strings such as "Published as a conference paper at ICLR 2026". The parser removes venue/status/header lines and drops pre-abstract author/header blocks before using the PDF text.

## 2. PDF Pipeline

Implemented files:

```text
koala_strategy/paper/pdf_cache.py
koala_strategy/paper/table_evidence.py
koala_strategy/llm/strong_judge.py
koala_strategy/models/fulltext_evidence_model.py
scripts/parse_pdfs.py
scripts/train_fulltext_evidence.py
```

Cache layout:

```text
data/pdf_cache/raw/{paper_id}.pdf
data/pdf_cache/parsed/{paper_id}.json
```

Parser output includes:

- sanitized full text
- section map
- references
- figure/table captions
- table evidence chunks
- parser warnings

Table evidence records include:

- `table_id`
- caption/context
- number of numeric values
- metric keywords
- baseline/comparison signal
- numeric density

## 3. Stronger Judge Features

The current stronger judge is deterministic and reproducible. It extracts:

- empirical depth
- rigor score
- theory depth
- reproducibility score
- novelty signal
- evidence risk
- reference grounding
- table strength
- `judge_accept_prior`

This avoids expensive LLM calls for every training paper. The `codex` provider remains configured for targeted future use, but the current experiment does not require GPU or external LLM calls.

## 4. Full-Text Model

V2 trains a full-text evidence head on parsed PDFs:

```text
features = [
  base_p_accept,
  base_uncertainty,
  base_logit,
  model_a_p,
  model_b_p,
  strong_judge_features,
  table_evidence_features,
  pdf length/section/reference features,
]
```

Models tested:

- HistGradientBoosting
- LogisticRegression
- RandomForest
- Safe sparse text evidence over title/abstract/table evidence
- OOF calibrated sparse text evidence with isotonic/Platt/none selection

The initial V2 default was LogisticRegression over deterministic full-text numeric evidence:

```text
p_final = 0.1 * p_base + 0.9 * p_fulltext
```

The blend alpha is selected by train OOF log loss, not by test labels.

The current V4 operational default is stricter:

```text
mode = evidence_tables_safe
payload = public title + public abstract + sanitized parsed table evidence
calibration = isotonic selected by train OOF log loss
p_final = 0.025 * p_base + 0.975 * p_safe_text_evidence
```

This excludes raw full-text/section TF-IDF from production because broad text models picked up post-decision PDF artifacts.

V4 also adds a local harness agent layer. Reviewer-component axes are used for internal role feedback and comment/verdict construction, but not as direct prediction features because they reduced held-out AUROC in both the paper-only and PDF numeric heads.

## 5. Current Experiment

PDF parse run:

```text
train_limit: 1200
train parsed successfully: 948
test parsed successfully: 339 / 349
```

On the same 339 test papers:

| model | Pearson vs accept | Spearman | AUROC | AUPRC | Brier | Log loss | Top 27% precision |
|---|---:|---:|---:|---:|---:|---:|---:|
| V1 base | 0.3231 | 0.3161 | 0.6824 | 0.6602 | 0.2242 | 0.6360 | 0.7174 |
| V2 HGB blend | 0.4364 | 0.4510 | 0.7612 | 0.7541 | 0.2185 | 0.6494 | 0.7717 |
| V2 logreg blend | 0.4497 | 0.4517 | 0.7616 | 0.7481 | 0.2090 | 0.6230 | 0.7609 |
| V2 RF | 0.4256 | 0.4322 | 0.7503 | 0.7389 | 0.2138 | 0.6232 | 0.7391 |
| V3 safe text-evidence blend | 0.5467 | 0.5379 | 0.8113 | 0.7579 | 0.1824 | 0.5590 | 0.7935 |
| V4 safe text-evidence blend | 0.5577 | 0.5539 | 0.8207 | 0.7656 | 0.1784 | 0.5474 | 0.8043 |

The default is V4 safe text-evidence blend because it has the best validated correlation, AUROC, Brier score, and log loss among non-leaky production candidates.

On all 349 test papers, using the V3 PDF model where parsed text is available and base fallback for the remaining 10 papers:

| model | Pearson vs accept | Spearman | AUROC | AUPRC | Brier | Log loss | Top 27% precision |
|---|---:|---:|---:|---:|---:|---:|---:|
| V1 base | 0.3252 | 0.3154 | 0.6817 | 0.6690 | 0.2248 | 0.6367 | 0.7128 |
| V3 safe text-evidence + fallback | 0.5404 | 0.5363 | 0.8101 | 0.7639 | 0.1842 | 0.5620 | 0.7979 |
| V4 safe text-evidence + fallback | 0.5511 | 0.5512 | 0.8187 | 0.7711 | 0.1803 | 0.5508 | 0.8085 |

## 6. Next Iteration

1. Retry remaining failed PDFs with slower backoff.
2. Increase train PDF corpus to 2500-5000 parsed papers.
3. Add LLM judge calls only for high-uncertainty papers or top candidate Koala papers.
4. Add table comparator logic to detect whether the proposed method is actually best in the table.
5. Add page/section references into comments automatically.
6. Re-run V3 calibration after more parsed training data.
