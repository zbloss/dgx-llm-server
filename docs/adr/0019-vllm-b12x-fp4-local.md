# ADR 0019: Serve NVIDIA's FP4-Local checkpoint with the community vLLM b12x image

**Status:** Accepted - unverified pending deploy
**Date:** 2026-10-23
**Amends:** ADR 0016, 0017, 0018 (backend, checkpoint, watchdog target)

## Context

NVIDIA published `nvidia/Qwen3.8-Flash-Next-FP4-Local` (2026-10-05), a checkpoint tested on DGX Spark whose model card gives a vLLM recipe. It needs a community container, `eugr/spark-vllm-b12x:latest` (Local Inference Lab), because upstream vLLM does not yet support the format (`--quantization modelopt_mixed --load-format b12x`, b12x GDN/linear/MoE kernels). The card lists vLLM as its only supported runtime.

## Decision

- Replace `sglang-server` with `vllm-server` on `eugr/spark-vllm-b12x:latest`, using the card's flags and environment verbatim (MTP with 4 speculative tokens, FP8 KV cache, `--max-model-len 262144`, `--max-num-seqs 8`, `--max-num-batched-tokens 4096`, `--gpu-memory-utilization 0.8`, `qwen3` reasoning parser, `qwen3_xml` tool parser, `--no-enable-flashinfer-autotune` which also matches ADR 0014).
- Keep the served model name `qwen3.8-flash-next` (`--served-model-name`) so clients are unaffected. Port stays 8000.
- Load from the pre-downloaded `/models/nvidia--Qwen3.8-Flash-Next-FP4-Local` with `HF_HUB_OFFLINE=1`, as before. `models/models.json` points at the new repo; `sync_models.py` removes the old NVFP4 directory.
- PLE offload is now `VLLM_PLE_TABLE_MEMORY=disk`; the SGLang entrypoint wrapper that wiped `/ple-cache` and the CI step that cleared it are removed. The card does not say where vLLM puts the table or whether it must be rebuilt per start, so no wipe is done.
- `mem-watchdog` now targets `dgx-llm-server-vllm-server-1`.
- Recipe deviations: container uses bridge networking with `8000:8000` instead of `--network host`; HF cache mount dropped (offline, local path).

## Consequences

- Depends on a community `:latest` image; pin a digest once a working one is verified.
- SGLang metrics (`sglang:*`) disappear; vLLM exposes `vllm:*` at `/metrics`. The home-server SGLang Grafana dashboard needs replacing.
- Unverified: first deploy may need the HF cache mount or `--network host` if the image misbehaves.
