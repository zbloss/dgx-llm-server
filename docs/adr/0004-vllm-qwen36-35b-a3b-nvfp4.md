# ADR 0004: Migrate to vLLM with Qwen3.6-35B-A3B-NVFP4

**Status:** Accepted
**Date:** 2026-06-15
**Supersedes:** ADR 0002

## Context

ADR 0002's llama.cpp stack ran Qwen3.6-27B (UD-Q4_K_XL GGUF). While functional, NVIDIA released native NVFP4 support for Qwen3.6-35B-A3B — a larger MoE model with 35B total / 3B active parameters — on the GB10 Blackwell GPU.

vLLM nightly provides Blackwell-optimized kernels (FlashInfer attention, Marlin MoE backend) and NVFP4 loading via `--load-format fastsafetensors`. The model is served from `nvidia/Qwen3.6-35B-A3B-NVFP4`.

## Decision

Replace the llama.cpp Server entirely with a single vLLM service:

- **Image:** `vllm/vllm-openai:nightly`
- **Model:** `nvidia/Qwen3.6-35B-A3B-NVFP4`
- **Port:** 8000 (changed from 8080)
- **Key flags:** `--kv-cache-dtype fp8`, `--attention-backend flashinfer`, `--moe-backend marlin`, `--gpu-memory-utilization 0.75`, `--max-model-len 131072`, prefix caching, chunked prefill, async scheduling, MTP speculative decoding (3 tokens, Triton backend)
- **Reasoning parser:** `--reasoning-parser qwen3`
- **Tool call parser:** `--tool-call-parser qwen3_xml`, `--enable-auto-tool-choice`

Remove `models/config.ini` (llama.cpp-only). Simplify `models/models.json` to a single entry. Update K8s manifests to port 8000 and `dgx-vllm` labels.

## Consequences

- Single vLLM container replaces llama.cpp. No model profile switching needed — one model, all phases.
- Port changes from 8080 to 8000. All clients update `OPENAI_BASE_URL` accordingly.
- Prometheus `/metrics` endpoint is safe — no `?model=` load trigger (that was a llama.cpp behavior).
- NVFP4 weights are loaded via fastsafetensors — significantly faster than GGUF deserialization.
- The sync script downloads the full HuggingFace repo (no `allow_patterns` filter for safetensors).
- Old GGUF repos in `/home/zbloss/models/` are cleaned up on next sync run.
