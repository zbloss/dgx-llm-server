# ADR 0011: vLLM perf tuning — GPU memory, batching, and reasoning effort

**Status:** Accepted
**Date:** 2026-08-16
**Amends:** ADR 0010 (no supersede — same model/image, config values updated)

## Context

ADR 0010 set `--gpu-memory-utilization 0.45` as a conservative starting point, noting it was "pre-tuned" without empirical justification. After running the service for 13+ hours (277 requests, 22M prompt tokens), Prometheus metrics revealed:

- **KV cache capacity:** only 514 blocks × 1616 tokens = 830,624 token KV cache
- **KV concurrency:** `kv_cache_max_concurrency = 2.79x` for 256K context — meaning the scheduler could barely hold 3 concurrent max-context sequences
- **Average TTFT:** 44s (median ~5-7s; long-tail 40-640s from complex reasoning + large contexts)
- **Prefix cache hit rate:** 56% — healthy, but cache churn limited by the small block count

The GB10 has 128 GB unified memory shared between CPU and GPU. At 0.45 × 128 = 57.6 GB reserved for vLLM, after model weights (~22 GiB NVFP4 + overhead = ~54 GB total process) only ~3.6 GB was left for KV cache. The OS and non-reclaimable processes use ~10 GB, leaving ~19 GB of headroom before swap at 0.85 utilization.

`--max-num-batched-tokens 8192` was also under-sized: with typical agent prompts at 80K tokens, prefill chunked into 8192-token steps bottlenecks time-to-first-token for large contexts.

Additionally, every container restart wiped the torch.compile and FlashInfer autotune cache (stored at `/root/.cache/vllm` with no host volume mount), causing a ~15 minute cold-start on every deploy.

Finally, Qwen3's reasoning model defaults to unbounded thinking depth. The server had no default reasoning effort set, allowing complex agent requests to consume hundreds of thinking tokens before producing any output.

## Decision

Update `compose.yaml` with the following changes:

| Parameter | Old | New | Reason |
|---|---|---|---|
| `--gpu-memory-utilization` | 0.45 | 0.85 | 15× more KV blocks; safe with ~19 GB OS headroom |
| `--max-num-seqs` | 4 | 8 | More concurrent sessions now that KV cache can hold them |
| `--max-num-batched-tokens` | 8192 | 32768 | Faster prefill for large agent contexts; matches compile range |
| `--default-chat-template-kwargs` | (unset) | `{"reasoning_effort":"medium"}` | Cap default thinking depth; clients can override per-request to `xhigh` |
| vLLM cache volume | (none) | `/home/zbloss/.cache/vllm:/root/.cache/vllm` | Persist compiled kernels across container restarts |

## Consequences

- **KV cache after change:** 1,318 blocks × 1616 tokens = 1,877,748 token cache; `kv_cache_max_concurrency = 7.16x`. This reduces cache eviction under concurrent agent load and improves prefix cache utilization.
- **Single-request decode speed** is unchanged (~31 tok/s) — this is hardware-bound, not config-bound.
- **Reasoning effort default `medium`:** reduces typical TTFAT from 44s average down to ~7-9s for moderate complexity requests. Clients running complex multi-step reasoning should pass `chat_template_kwargs: {"reasoning_effort": "xhigh"}` explicitly.
- **`--max-num-batched-tokens 32768`** matches the CUDA graph compile range (`Compiling a graph for compile range (1, 32768)`) — avoids recompilation for batch sizes up to 32768.
- **Cache volume:** first restart after this change still takes ~15 min (populates the host cache). Subsequent restarts reuse it and take ~3-4 min. Cache subdirectory names are version-hashed so image upgrades don't collide.
- OOM risk at 0.85: if other processes on the host grow significantly, swap could be hit. Monitor with `free -h`. If pressure is observed, drop to 0.82 (safely leaves ~23 GB for OS).
