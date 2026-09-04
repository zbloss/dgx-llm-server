#!/usr/bin/env bash
# Runs the guidellm benchmark (Dockerized, since guidellm's uvloop dependency
# doesn't build on Windows) against the DGX Spark from a separate machine, so
# the load generator doesn't share CPU/GPU with the server under test.
#
# Shaped for this deployment's actual traffic: agentic coding (Claude Code,
# Qwen Code CLI) and agentic use via the Hermes deployment - not generic
# chat. That means: a large first-turn prompt (system prompt + repo/file
# context), smaller follow-up turns (tool results, incremental instructions)
# to exercise --enable-prefix-caching the way a real session would, moderate
# code-sized outputs, and a small set of fixed concurrency levels (1, 2, 4)
# rather than a full saturation sweep up to hundreds of concurrent streams -
# this is a single-user/small-team deployment, not a public inference
# endpoint, so max-throughput numbers aren't the thing that matters.
#
# Usage: ./run.sh [duration_seconds_per_concurrency_level]
set -euo pipefail

MODEL_NAME="${MODEL_NAME:-qwen3.8-27b}"
TARGET="${TARGET:-http://192.168.68.104:8000}"
TOKENIZER_MODEL="${TOKENIZER_MODEL:-unsloth/Qwen3.8-27B-NVFP4}"
CONCURRENCY_LEVELS="${CONCURRENCY_LEVELS:-1,2,4}"
DURATION_SECONDS="${1:-${DURATION_SECONDS:-30}}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RESULTS_DIR="${SCRIPT_DIR}/results"
mkdir -p "${RESULTS_DIR}"

DATEKEY="$(date +%Y%m%d-%H%M%S)"
OUT_BASENAME="${MODEL_NAME}_${DATEKEY}"

docker build -t dgx-guidellm "${SCRIPT_DIR}" >/dev/null

# Git Bash (MSYS) rewrites leading-slash args like /results/... into Windows
# paths before they ever reach docker - MSYS_NO_PATHCONV disables that.
export MSYS_NO_PATHCONV=1

docker run --rm \
  -v "${RESULTS_DIR}:/results" \
  dgx-guidellm run \
  --backend kind=openai_http,target="${TARGET}" \
  --tokenizer kind=huggingface_auto,model="${TOKENIZER_MODEL}" \
  --profile kind=concurrent \
  --override profile.streams "${CONCURRENCY_LEVELS}" \
  --constraint kind=max_duration,seconds="${DURATION_SECONDS}" \
  --data kind=synthetic_text,prompt_tokens=600,prompt_tokens_stdev=300,output_tokens=350,output_tokens_stdev=200,turns=4,tool_call_turns=-1,tool_response_tokens=400,tool_response_tokens_stdev=200,'prefix_buckets=[{"prefix_tokens":5000}]' \
  --output kind=json,path="/results/${OUT_BASENAME}.json" \
  --output kind=csv,path="/results/${OUT_BASENAME}.csv"

echo "Results written to ${RESULTS_DIR}/${OUT_BASENAME}.{json,csv}"
