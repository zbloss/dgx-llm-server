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

Replace the vLLM + LiteLLM stack with a single llama.cpp Server running a single Model Profile in `models/config.ini`:

**`qwen3.6-27b-mtp`**
- Source: `unsloth/Qwen3.6-27B-MTP-GGUF` (UD-Q4_K_XL)
- Flags: `--spec-type draft-mtp --spec-draft-n-max 2 --mmproj <path> --parallel 4`
- Used for: all Agent Loop phases (Phases 1–5), including vision tasks via mmproj
- `--parallel 4` serves 2–4 concurrent agents without serial queuing

Drop LiteLLM proxy and Postgres DB entirely.

## Alternatives considered

**Keep vLLM two-model stack (ADR 0001):** Could not fit both models in 128 GB in practice due to KV cache requirements. Rejected.

**vLLM single model (Qwen3.6-27B):** Would require LiteLLM + Postgres stack with no routing benefit. llama.cpp's `--models-max 1` profile switching is a simpler fit for the single-user, phase-separated use case. Rejected.

**Qwen3.5-122B-A10B as a single model:** Lower coding benchmarks (78.9% LiveCodeBench vs 83.9%, 72.0% SWE-bench vs 77.2%). Rejected on quality grounds.

**Nemotron + vision model with llama.cpp:** Nemotron scores lower than Qwen3.6-27B on coding benchmarks and has no vision capability, requiring a second model download with no quality benefit. Rejected.

## Consequences

- Context window is 262K natively, with ~110 GB available for KV cache. Sessions that were already compacting at ~130K are unaffected; longer sessions gain headroom.
- `--parallel 4` allows 2–4 concurrent agents to be served simultaneously. KV cache is unified across slots (`kv_unified = true`).
- vLLM's Blackwell-optimized NVFP4 kernels are no longer used. llama.cpp's CUDA backend is less optimized for the GB10. MTP speculative decoding (~1.5–2× speedup on coding tasks) compensates for this on the primary workload.
- llama.cpp supports `--mmproj` alongside active MTP decoding. A single GGUF download handles all phases including vision.
- Docker Compose stack reduces from four services (litellm, db, vllm-nemotron, vllm-qwen3vl) to one (llama-server).
- NVFP4 HuggingFace checkpoints are superseded by GGUF Checkpoints. The GitOps Workflow continues to manage downloads and restarts; `models/models.json` remains the source of truth for which models are active.
- Any HTTP request to `/metrics?model=<name>` triggers a model load. Prometheus scraping must omit the `?model=` parameter. See ADR 0003.
