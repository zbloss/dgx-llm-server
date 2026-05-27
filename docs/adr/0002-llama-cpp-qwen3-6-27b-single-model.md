# ADR 0002: Return to llama.cpp with Qwen3.6-27B

**Status:** Accepted  
**Date:** 2026-05-27  
**Supersedes:** ADR 0001

## Context

ADR 0001's two-model vLLM stack (Nemotron + Qwen3-VL-32B) could not run in practice. Nemotron required significantly more GPU memory for KV cache than the 0.55 `--gpu-memory-utilization` allocation allowed; raising it to 0.85 left no room for the second model. The combined allocation could not fit in 128 GB.

While investigating alternatives, benchmark research revealed that Qwen3.6-27B strictly dominates the previous model choices:

| Model | LiveCodeBench v6 | SWE-bench Verified | Vision |
|---|---|---|---|
| Nemotron-3-Super (NVFP4) | 78.44% | — | No |
| Qwen3-VL-32B (NVFP4) | — | — | Yes |
| **Qwen3.6-27B** | **83.9%** | **77.2%** | **Yes** |

Qwen3.6-27B outperforms Nemotron on coding benchmarks and includes a built-in vision encoder, eliminating the need for two separate models entirely. At 27B parameters with UD-Q4_K_XL quantization (~14–18 GB weights), it leaves ~110 GB of the 128 GB budget for KV cache — far more than the memory-starved two-model setup provided either model.

The agent loop is phase-separated: QA Validation (Phase 5) always runs after Phases 1–4 complete. One model profile switch per project cycle is the maximum. llama.cpp's `--models-max 1` flag enables transparent automatic switching: when a client sends a request with a different `model` name, the server unloads the current model and loads the requested one — no orchestration code required.

The advisor-escalation pattern from ADR 0001 (QA Agent escalating to a text model mid-session) was also re-evaluated and confirmed as never implemented. It is removed from the design.

## Decision

Replace the vLLM + LiteLLM stack with a single llama.cpp Server. Run Qwen3.6-27B as two Model Profiles in `models/config.ini`:

**MTP Profile** — `qwen3.6-27b-mtp`
- Source: `unsloth/Qwen3.6-27B-MTP-GGUF` (UD-Q4_K_XL)
- Flags: `--spec-type draft-mtp --spec-draft-n-max 2`
- Used for: Phases 1–4 (planning, implementation, TDD, merge)
- No mmproj (llama.cpp does not support `--mmproj` alongside active MTP decoding)

**Vision Profile** — `qwen3.6-27b`
- Source: `unsloth/Qwen3.6-27B-GGUF` (UD-Q4_K_XL) + `mmproj-BF16.gguf`
- Flags: `--mmproj /models/unsloth--Qwen3.6-27B-GGUF/mmproj-BF16.gguf`
- Used for: Phase 5 (QA Validation)

Drop LiteLLM proxy and Postgres DB entirely.

## Alternatives considered

**Keep vLLM two-model stack (ADR 0001):** Could not fit both models in 128 GB in practice due to KV cache requirements. Rejected.

**vLLM single model (Qwen3.6-27B):** Would require LiteLLM + Postgres stack with no routing benefit. llama.cpp's `--models-max 1` profile switching is a simpler fit for the single-user, phase-separated use case. Rejected.

**Qwen3.5-122B-A10B as a single model:** Lower coding benchmarks (78.9% LiveCodeBench vs 83.9%, 72.0% SWE-bench vs 77.2%). Rejected on quality grounds.

**Nemotron + vision model with llama.cpp:** Nemotron scores lower than Qwen3.6-27B on coding benchmarks and has no vision capability, requiring a second model download with no quality benefit. Rejected.

## Consequences

- Context window increases from 131K (vLLM cap from ADR 0001) to 262K natively, with ~110 GB available for KV cache. Sessions that were already compacting at ~130K are unaffected; longer sessions gain headroom.
- vLLM's Blackwell-optimized NVFP4 kernels are no longer used. llama.cpp's CUDA backend is less optimized for the GB10. For single-user homelab load, the throughput difference is acceptable.
- Two GGUF downloads are required (MTP GGUF and regular GGUF) because llama.cpp does not currently support `--mmproj` alongside active MTP decoding. If a future llama.cpp release lifts this restriction, the Vision Profile can be pointed at the MTP GGUF and the second download eliminated.
- Docker Compose stack reduces from four services (litellm, db, vllm-nemotron, vllm-qwen3vl) to one (llama-server).
- NVFP4 HuggingFace checkpoints are superseded by GGUF Checkpoints. The GitOps Workflow continues to manage downloads and restarts; `models/models.json` remains the source of truth for which models are active.
