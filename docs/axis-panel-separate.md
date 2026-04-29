# Axis Panel Separate

`axis-panel-separate` preserves the current axis-panel paper review as a fully paper-only stage, then runs a separate bounded calibrator against category-level priors from non-eval ICLR 2026 official reviews.

## Workflow

1. Stage A reads the paper locally and emits `base_result.json`.
2. The priors builder aggregates per-category score distributions, confidence priors, axis concern frequencies, and typical severity patterns, then writes `review_priors_by_category.json`.
3. Stage B loads only the target paper's relevant category priors and runs a separate calibrator over `base_result.json`.
4. The calibrator can adjust the score by at most `±0.5` and the decision band by at most one step.
5. Final `result.json` keeps the same public paper-review fields as the paper-only harness and adds calibration metadata such as `score_delta`, `band_changed`, and `memory_used`.

## Safety rules

- Only `official_review` rows are used.
- All 100 eval `forum_id`s from `experimental/results/axis_panel_master_backtest_iclr2026_100/input_manifest.jsonl` are excluded from the priors artifact.
- Memory is consulted only after the five-axis paper-first synthesis.
- The priors are not evidence for the target paper; the paper wins on factual claims.

## Build

```bash
python3 experimental/build_iclr2026_review_priors.py
```

## Backtest

```bash
python3 experimental/backtest_axis_panel_separate.py --manifest experimental/results/axis_panel_master_backtest_iclr2026_100/input_manifest.jsonl --workers 2
```

## Live launch note

`reva launch` compiles only the global prompt, platform skills, and `agent_configs/axis-panel-separate/system_prompt.md`. The live agent therefore must execute the separate calibration stage from its prompt instructions: read the paper first, write the paper-only Stage A reasoning, then load `experimental/artifacts/axis_panel_separate/review_priors_by_category.json` and apply the bounded calibrator rules from `agent_configs/axis-panel-separate/calibrator_prompt.md`.

Before replacing another live agent, copy or provision `agent_configs/axis-panel-separate/.api_key`, then run:

```bash
uv run reva launch --name axis-panel-separate --cluster
```
