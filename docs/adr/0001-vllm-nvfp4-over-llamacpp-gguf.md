# ADR-0001: vLLM + NVFP4 over llama.cpp + GGUF

## Status
Accepted

## Context

The DGX Spark (GB10 Grace Blackwell, 128 GB unified memory) was initially running llama.cpp in router mode serving GGUF-quantized models. GGUF was chosen because full BF16/FP16 HuggingFace weights for 35B+ MoE models exceed the 128 GB memory ceiling.

vLLM was identified as significantly more efficient on Blackwell hardware for serving throughput. However, vLLM's primary format is HuggingFace model repos — not GGUF. GGUF support in vLLM is experimental and not the performance path.

The two serving models are Qwen3.6-35B-A3B (~37 GB at Q8_0 GGUF) and Qwen3.6-27B (~29 GB at Q8_0 GGUF).

## Decision

Migrate from llama.cpp (GGUF) to vLLM (NVFP4 HuggingFace checkpoints).

NVFP4 is a 4-bit floating-point format native to Blackwell Tensor Cores. It resolves the memory constraint that originally forced GGUF:

- `RedHatAI/Qwen3.6-35B-A3B-NVFP4`: ~21.9 GB (vs 37 GB at Q8_0)
- `unsloth/Qwen3.6-27B-NVFP4`: ~15 GB (vs 29 GB at Q8_0)
- Combined: ~37 GB, leaving ~91 GB for KV cache across both instances

Both models run simultaneously with full 262,144-token context enabled by `--kv-cache-dtype fp8`.

## Consequences

- **GitOps workflow changes**: `models.json` drops `hf_filename` (NVFP4 is a full repo snapshot); sync script switches from `hf_hub_download()` to `snapshot_download()`.
- **Model format is now hardware-specific**: NVFP4 requires Blackwell. Moving to a non-Blackwell machine would require re-quantizing to a different format.
- **Throughput improvement**: vLLM's continuous batching and Blackwell-optimized CUDA kernels substantially outperform llama.cpp for serving workloads.
- **llama.cpp router mode is no longer used**: the `model` field routing is handled by the LiteLLM Proxy (see ADR-0002).
