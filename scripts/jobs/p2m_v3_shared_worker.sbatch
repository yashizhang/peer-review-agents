#!/usr/bin/env bash
#SBATCH -J p2m_v3_worker
#SBATCH --gres=gpu:a100l:1
#SBATCH -c 6
#SBATCH --mem=32G
#SBATCH -t 12:00:00

set -euo pipefail

RUN_ROOT="${RUN_ROOT:-data/processed_papers}"
RUN_NAME="${RUN_NAME:?}"
MANIFEST="${MANIFEST:?}"
SHARD_INDEX="${SHARD_INDEX:?}"
SHARD_COUNT="${SHARD_COUNT:?}"
WORKER_ID="${WORKER_ID:-worker_${SHARD_INDEX}}"
MARKER_BIN="${MARKER_BIN:-marker_single}"
MARKER_TIMEOUT="${MARKER_TIMEOUT_SECONDS:-240}"
MAX_RETRIES="${MAX_RETRIES:-2}"

LOG_DIR="${RUN_ROOT}/${RUN_NAME}/logs"
mkdir -p "${LOG_DIR}"

PROJECT_ROOT="${PROJECT_ROOT:-/home/mila/j/jianan.zhao/ReviewAgent}"
cd "${PROJECT_ROOT}"
source /home/mila/j/jianan.zhao/scratch/Utils/shell/init.sh ReviewAgent

export LD_LIBRARY_PATH=/cvmfs/ai.mila.quebec/apps/x86_64/debian/anaconda/3/lib:${LD_LIBRARY_PATH:-}
export XDG_CACHE_HOME=/network/scratch/j/jianan.zhao/.cache/reviewagent
export TRITON_CACHE_DIR=/network/scratch/j/jianan.zhao/.cache/reviewagent/triton
export TMPDIR=/network/scratch/j/jianan.zhao/tmp/reviewagent
export HF_HOME=/network/scratch/j/jianan.zhao/.cache/reviewagent/huggingface
export HUGGINGFACE_HUB_CACHE=/network/scratch/j/jianan.zhao/.cache/reviewagent/huggingface/hub
export TRANSFORMERS_CACHE=/network/scratch/j/jianan.zhao/.cache/reviewagent/huggingface/transformers
export HF_DATASETS_CACHE=/network/scratch/j/jianan.zhao/.cache/reviewagent/huggingface/datasets
export TORCH_EXTENSIONS_DIR=/network/scratch/j/jianan.zhao/.cache/reviewagent/torch_extensions
export WANDB_CACHE_DIR=/network/scratch/j/jianan.zhao/.cache/reviewagent/wandb
export TORCH_DEVICE=cuda

cd /home/mila/j/jianan.zhao/ReviewAgent
.venv/bin/python scripts/jobs/p2m_v3_shared_worker.py \
  --run-root "${RUN_ROOT}" \
  --run-name "${RUN_NAME}" \
  --manifest "${MANIFEST}" \
  --shard-index "${SHARD_INDEX}" \
  --shard-count "${SHARD_COUNT}" \
  --marker-binary "${MARKER_BIN}" \
  --marker-timeout-seconds "${MARKER_TIMEOUT}" \
  --max-retries "${MAX_RETRIES}" \
  --worker-id "${WORKER_ID}"
