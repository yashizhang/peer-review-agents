# Calibration Card

Model version: iclr26_v1

## Summary

ICLR26 paper-only predictor with a heuristic pseudo-review stacker and a discussion-aware proxy model.

## Leakage Policy

Model A/B use only paper-visible fields. Model C uses official reviews only as proxy discussion training features, not for online paper-only inference.

## Held-Out Global Test

- AUROC: 0.6817
- AUPRC: 0.6690
- Brier: 0.2248
- Log loss: 0.6367
- Top 27% precision: 0.7184
- Suggested score MAE: 1.3256

## Training Set

- Examples: 13378
- Accept: 5187
- Reject: 8191
