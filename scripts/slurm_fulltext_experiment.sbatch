#!/usr/bin/env bash
#SBATCH -J koala-fulltext
#SBATCH --gres=gpu:a100l:1
#SBATCH -c 6
#SBATCH --mem=32G
#SBATCH -t 48:00:00
#SBATCH --partition=unkillable

set -euo pipefail
cd /network/scratch/l/liux/peer-review-agents

.venv/bin/python -m koala_strategy.cli parse-pdfs --train-limit "${TRAIN_LIMIT:-1200}" --workers 6
.venv/bin/python -m koala_strategy.cli train-fulltext-evidence --train-limit "${TRAIN_LIMIT:-1200}" --model-type logreg

