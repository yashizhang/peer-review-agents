# Koala Science Review Agents

This repository implements an ICLR26-calibrated review-agent backend for the Koala Science agent review setting.

The current workspace did not include the original Koala starter code, so this implementation provides a standalone `koala_strategy` package with dry-run-safe platform wrappers, one public `review_director` agent, internal subagent personas, training scripts, prediction bundles, reasoning files, SQLite cache, JSONL logs, tests, and dashboard output.

## Environment

All local installs are kept under this repository:

```bash
python -m venv .venv
.venv/bin/python -m pip install -e '.[test]'
```

GPU is not required for the implemented predictor. `cluster.md` documents the available A100 allocation if heavier PDF/LLM inference is added later.

## Data

The implementation uses the provided data under:

```text
data/koala_iclr2026/
```

Training uses `global_train.jsonl`. Offline evaluation uses `global_test_public.jsonl` plus `global_test_labels.jsonl`.

Model A/B paper-only inference uses only public paper-visible fields such as title, abstract, keywords, domain, and derived structural/text features. Model C uses official ICLR reviews only as proxy discussion features during training.

## Commands

Build processed examples:

```bash
.venv/bin/python -m koala_strategy.cli build-iclr-dataset
```

Train all models and evaluate the held-out test set:

```bash
.venv/bin/python -m koala_strategy.cli train-all
```

Parse PDFs and train the V2 full-text evidence head:

```bash
.venv/bin/python -m koala_strategy.cli parse-pdfs --train-limit 1200 --workers 3
.venv/bin/python -m koala_strategy.cli train-fulltext-evidence --train-limit 1200 --model-type logreg
.venv/bin/python -m koala_strategy.cli train-fast-text-evidence --train-limit 2000 --mode sections_safe
```

Score one public paper:

```bash
.venv/bin/python -m koala_strategy.cli score-paper PklMD8PwUy
```

Dry-run a comment:

```bash
.venv/bin/python -m koala_strategy.cli dry-run-one-paper PklMD8PwUy --agent calibrated_decider
```

Dry-run a verdict with mock external comments:

```bash
.venv/bin/python -m koala_strategy.cli dry-run-verdict PklMD8PwUy --agent calibrated_decider
```

Run the cached LLM brain judge on one paper:

```bash
.venv/bin/python -m koala_strategy.cli score-paper-llm PklMD8PwUy
```

Run a limited LLM judge experiment on hard held-out test papers:

```bash
.venv/bin/python -m koala_strategy.cli llm-judge-subset --limit 8 --selection balanced_uncertain
```

Dashboard:

```bash
.venv/bin/python -m koala_strategy.cli dashboard
```

Tests:

```bash
.venv/bin/python -m pytest
```

## Artifacts

Model artifacts are written to:

```text
models/iclr26_v1/
```

Prediction bundles and logs are written under ignored data paths:

```text
data/koala_cache/predictions/
data/logs/
```

Reasoning files are written to:

```text
reasoning/<agent>/<paper_id>/
```

Real GitHub publishing is disabled by default. Dry runs return `dry-run://...` URLs. To publish for real, configure `github.publish_enabled`, `github.repo`, and `github.raw_base_url` in `strategy_config.yaml`.

## Current Held-Out Test Result

V1 title/abstract model on 349 held-out public test papers:

```text
AUROC: 0.6817
AUPRC: 0.6690
Brier: 0.2248
Log loss: 0.6367
Top 27% precision: 0.7184
Suggested score MAE: 1.1357
Mean predicted accept: 0.4219
Mean uncertainty: 0.3591
```

The strongest operational metric here is the top-slice precision: among the papers the model ranks in the top 27%, 71.8% are accepted in the held-out labels.

Current V6 PDF text-evidence enhancement on the 339 test papers with parsed PDFs.
This uses sanitized raw sections/full text plus table evidence; obvious
post-decision, author-identity, OpenReview/status, acknowledgement, and
camera-ready artifacts are removed before modeling or LLM use:

```text
V1 base AUROC on same subset: 0.6824
V6 sections-safe text-evidence AUROC: 0.9347
V6 sections-safe text-evidence AUPRC: 0.8790
V6 sections-safe text-evidence Brier: 0.0883
V6 sections-safe text-evidence Log loss: 0.3044
V6 sections-safe text-evidence Pearson vs accept: 0.8112
V6 sections-safe text-evidence Spearman vs accept: 0.8040
V6 sections-safe text-evidence Top 27% precision: 0.8913
```

On the full 349-paper test set, using the PDF model for 339 parsed PDFs and base fallback for 10 missing PDFs:

```text
Pearson vs accept: 0.7990
Spearman vs accept: 0.7927
AUROC: 0.9308
AUPRC: 0.8773
Brier: 0.0928
Log loss: 0.3147
Top 27% precision: 0.8830
```

LLM brain note: `review_director` now calls a cached Codex/GPT-5.4-mini
structured judge when `models.use_llm_harness=true`. On an 8-paper
balanced-uncertain diagnostic subset, the non-LLM base AUROC was `0.5625`; the
LLM gated blend reached `0.8125` AUROC and improved Pearson from `0.3704` to
`0.5954`. Comment drafts are also polished by GPT-5.4-mini before posting.
This is a hard-subset diagnostic, not a full-test LLM result.

See [plan_v2.md](plan_v2.md) for the revised one-agent architecture and experiment details.
