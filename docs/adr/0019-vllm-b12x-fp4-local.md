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

## Deploy findings (2026-10-23)

The first start was killed by `mem-watchdog` at 12:36 (`MemAvailable` 3.5 GB, just after the 71.7 GB of weights loaded, during b12x GEMM autotune). A manual second start succeeded: ~14 minutes from `docker start` to `/v1/models` 200, `healthy`, no restarts, MTP accepting drafts (19/32 on the smoke request), KV cache 18.01 GiB (957,934 tokens, 3.65x at 262144) at `--gpu-memory-utilization 0.8`.

- **Startup is dominated by b12x autotune** (two passes: 241 GEMM/MoE shapes, then 209 GDN-prefill shapes, ~800 and ~2,000+ compilations). `MemAvailable` swung between ~5.7 GiB and ~38 GiB during it, so the 4 GiB watchdog threshold has little margin. Whether the b12x cache under `/home/zbloss/.cache/b12x` persists across restarts is unverified (the second pass logged `9 cached`; the first logged `0 cached`).
- **`docker kill` does not restart a `restart: unless-stopped` container**, contrary to ADR 0018's assumption: after the watchdog fired the service stayed `Exited (137)` until started by hand.
- **The `table_memory='device'` log line is not the PLE setting.** It comes from the generic Engram config; this model resolves `VLLM_PLE_TABLE_MEMORY` in its own `b12x_ple.py`.

### Changes

- `mem-watchdog` now runs `docker restart -t 0` instead of `docker kill`, so a trip self-heals. Risk: if startup dips below the threshold every time, it restarts in a loop (one attempt per `COOLDOWN_SECONDS` + startup).
- `--gpu-memory-utilization` 0.8 -> 0.75 for ~6 GiB more host headroom. Expected cost: KV cache shrinks from ~18 GiB to roughly 12 GiB (about 2x concurrency at full 262144 context instead of 3.65x). Unmeasured; revisit after the next start.
- Image pinned to digest `sha256:03a956544e9f9b4b7ba91690469fbe7848bdbef29a7d2e0f5788368467bb3595` (the build that was verified) instead of `:latest`.
