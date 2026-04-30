#!/usr/bin/env bash
#SBATCH --job-name=ajz112-llm-judge
#SBATCH --partition=long-cpu
#SBATCH -t 08:00:00
#SBATCH -c 1
#SBATCH --mem=8G
#SBATCH --output=/network/scratch/j/jianan.zhao/ReviewAgent/data/llm_judge/logs/%x_%j.out
#SBATCH --error=/network/scratch/j/jianan.zhao/ReviewAgent/data/llm_judge/logs/%x_%j.err

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${PROJECT_ROOT}"
source /network/scratch/j/jianan.zhao/Utils/shell/init.sh ReviewAgent

if [[ -f .env ]]; then
  set -a
  source .env
  set +a
fi

export LD_LIBRARY_PATH=/cvmfs/ai.mila.quebec/apps/x86_64/debian/anaconda/3/lib:${LD_LIBRARY_PATH:-}
export DEEPSEEK_MAX_RETRIES="${DEEPSEEK_MAX_RETRIES:-5}"
export DEEPSEEK_RETRY_BACKOFF_SECONDS="${DEEPSEEK_RETRY_BACKOFF_SECONDS:-5.0}"

export KOALA_LLM_PROVIDER="${LLM_PROVIDER:-${KOALA_LLM_PROVIDER:-deepseek}}"
export KOALA_CODEX_MODEL="${CODEX_MODEL:-${KOALA_CODEX_MODEL:-gpt-5.5}}"
if [[ "${KOALA_LLM_PROVIDER}" == "deepseek" && "${KOALA_CODEX_MODEL}" == "gpt-5.5" && -n "${DEEPSEEK_MODEL:-}" ]]; then
  export KOALA_CODEX_MODEL="${DEEPSEEK_MODEL}"
fi
export KOALA_CODEX_AUTH_FILE="${CODEX_AUTH_FILE:-${KOALA_CODEX_AUTH_FILE:-}}"
export KOALA_LLM_JUDGE_PROMPT_PROFILE="${PROMPT_PROFILE:-${KOALA_LLM_JUDGE_PROMPT_PROFILE:-contrastive_domain_v2}}"

LIMIT="${LIMIT:-139}"
SELECTION="${SELECTION:-random}"
SEED="${SEED:-42}"
WORKERS="${WORKERS:-1}"
PROMPT_PROFILE="${KOALA_LLM_JUDGE_PROMPT_PROFILE}"
FORCE="${FORCE:-0}"

if [[ "${FORCE}" == "1" || "${FORCE}" == "true" || "${FORCE}" == "yes" ]]; then
  FORCE_FLAG="--force"
else
  FORCE_FLAG=""
fi

mkdir -p data/llm_judge/logs
echo "start_at=$(date -Is)"
echo "job_id=${SLURM_JOB_ID:-none}"
echo "host=$(hostname)"
echo "commit=$(git rev-parse --short HEAD)"
echo "provider=${KOALA_LLM_PROVIDER}"
echo "model=${KOALA_CODEX_MODEL}"
echo "prompt_profile=${PROMPT_PROFILE}"
echo "selection=${SELECTION} limit=${LIMIT} seed=${SEED} workers=${WORKERS} force=${FORCE}"

PYTHON_BIN="${PROJECT_ROOT}/.venv/bin/python"
if [[ -x "${PYTHON_BIN}" ]]; then
  "${PYTHON_BIN}" -m koala_strategy.cli llm-judge-subset \
    --limit "${LIMIT}" \
    --selection "${SELECTION}" \
    --seed "${SEED}" \
    --workers "${WORKERS}" \
    --prompt-profile "${PROMPT_PROFILE}" \
    ${FORCE_FLAG}
else
  python -m koala_strategy.cli llm-judge-subset \
    --limit "${LIMIT}" \
    --selection "${SELECTION}" \
    --seed "${SEED}" \
    --workers "${WORKERS}" \
    --prompt-profile "${PROMPT_PROFILE}" \
    ${FORCE_FLAG}
fi

echo "finish_at=$(date -Is)"
