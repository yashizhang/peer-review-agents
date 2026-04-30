# Spec: AJZ-112 Verdict Predictor

## Goal
Build a DeepSeek-backed structured-feature pipeline over processed ICLR papers and train calibrated accept-probability models from those features.

## Context
- `data/processed_papers/iclr26/{paper_id}` contains Paper2Markdown-V3 artifacts, with `model_text_v3.txt` as the default model-facing paper text.
- `data/koala_iclr2026/global_train.jsonl` contains labels and official review text for train papers; held-out public papers do not expose official review text.
- Existing LLM provider routing lives in `koala_strategy/llm/providers.py`; `KOALA_LLM_PROVIDER=deepseek` is the new provider path.

## Requirements
1. Add self-review structured feature extraction from processed paper markdown using DeepSeek-compatible provider calls.
2. Add external review evaluation that merges review reliability, weighted component summaries, reliability summaries, and self-external gaps into one cached output.
3. Support current `data/processed_papers/iclr26` scope first; run external evaluator only where official review text exists.
4. Train calibrated models from structured features with Logistic Regression always available and LightGBM/XGBoost optional when installed.
5. Add CLI commands for extracting self-review features, extracting review-evaluator features, and training structured verdict models.

## Constraints
- Do not include decisions, labels, official reviews, review scores, or source status in self-review prompts.
- Do not write or print API key values.
- Cache outputs under ignored `data/` paths and make extraction resumable.
- Keep Codex provider intact for existing workflows, but do not use it for DeepSeek.

## Test Plan
- Mock provider tests for DeepSeek payload and selection.
- Unit tests for self-review prompt leakage guard, schema validation, cache resume, and feature flattening.
- Unit tests for review evaluator missing-review behavior and gap feature construction.
- Unit tests for structured model training on synthetic features with sklearn fallback.
- Run full local pytest after implementation.

## Acceptance Criteria
- `python -m koala_strategy.cli extract-self-review-features --subset iclr26 --limit 10` can run with a mocked provider and writes stable JSONL/cache rows.
- `python -m koala_strategy.cli extract-review-evaluator-features --subset iclr26 --limit 5` skips papers without official reviews and emits merged external/gap rows for train papers.
- `python -m koala_strategy.cli train-structured-verdict-model` writes calibrated metrics and model artifacts using cached structured features.
- All relevant tests pass and `git diff --check` passes.
