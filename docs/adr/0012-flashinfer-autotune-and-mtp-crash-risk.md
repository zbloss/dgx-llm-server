# ADR 0012: FlashInfer autotune, and a documented MTP crash risk

**Status:** Accepted — `--enable-flashinfer-autotune` unverified pending deploy
**Date:** 2026-09-04
**Amends:** ADR 0010/0011 (same model/image, config values updated)

## Context

The operator surfaced a community claim of 60-79 tok/s on `Qwen3.8-27B-NVFP4` via a `vllm serve` recipe using `CUTE_DSL_ARCH`/`FLASHINFER_CUDA_ARCH_LIST`/`FLASHINFER_DISABLE_VERSION_CHECK`/`PYTORCH_CUDA_ALLOC_CONF` env vars, `VLLM_USE_FLASHINFER_SAMPLER=1`, `--enable-flashinfer-autotune`, and `num_speculative_tokens: 3` (vs. our `5`), plus a Kubesimplify blog post on the same model/hardware. Both were researched against our actual image and config before adopting anything, following the precedent set by ADR 0008 (don't copy third-party tuning numbers without checking they apply here).

**Findings:**

- No source actually reproduced 60-79 tok/s **single-request** decode. The real single-stream numbers found (22-33.7 tok/s across sources) are consistent with our own ADR-0011 measurement of ~31 tok/s, which that ADR already characterized as hardware-bound. The 60-79+ figures in circulation are **aggregate throughput at 10-16 concurrent streams**, not single-request speed — not directly comparable to our per-request benchmark (`scripts/benchmark.py`).
- Our image (`ghcr.io/spark-arena/dgx-vllm-eugr-nightly`) is very likely built from `eugr/spark-vllm-docker`, which bakes `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True` and `FLASHINFER_CUDA_ARCH_LIST=12.1a`/`TORCH_CUDA_ARCH_LIST=12.1a` in **at build time** by default. Setting `CUTE_DSL_ARCH`, `FLASHINFER_CUDA_ARCH_LIST`, `FLASHINFER_DISABLE_VERSION_CHECK`, or `PYTORCH_CUDA_ALLOC_CONF` again at the compose level would most likely be redundant no-ops for this image. Not adopted.
- `VLLM_USE_FLASHINFER_SAMPLER=1` is documented as vLLM's default on supported hardware already. Not adopted — no expected effect.
- `--enable-flashinfer-autotune` is a real, confirmed vLLM flag (benchmarks FlashInfer kernel variants at warmup and caches the best one, vs. a heuristic fallback) and is genuinely new territory for our config — not baked into the image, not previously set. Plausible real throughput win. **Adopted, unverified pending deploy** (same posture as ADR 0007/0008): watch startup logs for an unrecognized-argument failure on our exact pinned image build; revert this one flag if it doesn't start.
- A third-party blog reports MTP speculative decoding on vLLM (same nightly build family) causing a **full host hard-reboot** at ~16K context with 2 concurrent requests, on both FP8 and NVFP4 quants of this model. Our production config already runs MTP (`num_speculative_tokens: 5`) at `--max-model-len 262144`, so this is an existing exposure, not a hypothetical one we'd be opting into.
- `num_speculative_tokens: 3` (the community recipe) vs. our `5` is a genuine tradeoff (fewer draft tokens = lower rejection overhead per step, but potentially lower speedup if acceptance rate is high) — not adopted this round; would need an A/B measurement with `scripts/benchmark.py` under clean conditions to justify, per the ADR-0008 precedent of not trusting an unverified third-party number.
- `--gpu-memory-utilization 0.82` (community) vs. our `0.85`: our value is the result of ADR-0011's own empirical measurement (15x KV cache block growth), which outranks a generic third-party default. Not changed.
- `kv_cache_dtype: fp8` (mentioned in the Kubesimplify recipe) would roughly halve KV cache memory, freeing room for more concurrent sequences — a real lever, but it's a quality/latency tradeoff requiring its own validation, not a drop-in win. Out of scope for this ADR; flagged as a candidate follow-up.
- `--limit-mm-per-prompt` (community recipe) is multimodal-only and not applicable — this deployment serves a text-only NVFP4 checkpoint (see `CONTEXT.md`'s QA Agent entry).

## Decision

- Add `--enable-flashinfer-autotune` to `compose.yaml`. Unverified pending deploy — watch `docker compose logs` on the next GitOps sync for a startup failure; if the flag is unrecognized on this image's exact vLLM build, revert this one line.
- No other flags or env vars changed. Speculative-decoding token count (`5`), `--gpu-memory-utilization` (`0.85`), and the FlashInfer/CUDA-arch env vars from the community recipe are deliberately left as-is — see Context for why each was rejected.
- **Document, do not act on, the MTP crash report.** The operator chose to keep MTP as configured rather than reduce `num_speculative_tokens` or disable speculative decoding, given no first-hand incident has occurred on this deployment. This is a known-risk acceptance, not a resolved issue — see Consequences.

## Consequences

- If `--enable-flashinfer-autotune` isn't recognized by this image's vLLM build, the container will fail its healthcheck (`start_period: 240s`) and `docker compose up -d` will show it unhealthy/restarting — check logs first, then drop the flag and redeploy.
- If the flag does start cleanly, watch for a longer cold-start on first boot (kernel autotuning happens at warmup, on top of the existing torch.compile/FlashInfer cache warm from ADR-0011's `/root/.cache/vllm` volume) before judging steady-state throughput.
- **Standing risk, not mitigated by this ADR:** MTP speculative decoding has one third-party report of causing a full DGX Spark host reboot under concurrent load at large context on this same model/nightly-build family. If a host-level crash or unexplained reboot occurs, check this ADR and consider dropping `--speculative-config` entirely as the next step, before assuming it's an unrelated hardware fault.
- `README.md`'s "Model" section still says `gpu-memory-utilization 0.45` — stale since ADR-0011 changed it to `0.85`; corrected alongside this ADR since it was noticed in the same review pass.
- Any future A/B test of `num_speculative_tokens` (3 vs. 5) should use `scripts/benchmark.py` under clean conditions (no concurrent large-prefill traffic on the other slot — see ADR-0008's caution about confounded measurements) before changing the deployed value.
