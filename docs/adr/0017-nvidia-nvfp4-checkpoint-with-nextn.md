# ADR 0017: Switch Qwen3.8-Flash-Next checkpoint from RadixArk to NVIDIA NVFP4, enable NEXTN speculative decoding

**Status:** Accepted — unverified pending deploy
**Date:** 2026-09-16
**Amends:** ADR 0016 (checkpoint choice and reasoning/tool-call parser decision, from that ADR's "Decision" section)

## Context

While deploying ADR 0016 (wayfinder map [#28](https://github.com/zbloss/dgx-llm-server/issues/28), ticket [#33](https://github.com/zbloss/dgx-llm-server/issues/33)), a home internet outage left the `sync-models.yml` run's `snapshot_download()` hung on a dead connection (`CLOSE_WAIT` socket, process blocked on `futex_do_wait`, disk usage and CPU time both flat for 50+ minutes after the outage). The run was cancelled and the process killed. Before restarting the download, SGLang's official cookbook page for this exact model — `https://docs.sglang.io/cookbook/autoregressive/Qwen/Qwen3.8-Flash-Next` — was checked live (via browser, clicking through the Hardware=DGX Spark / Quantization=NVFP4 (NVDA) / Nodes=Single selectors, not just reading a pasted excerpt) and found to now carry a **Verified** single-DGX-Spark recipe that didn't exist at ADR 0016/ticket [#30](https://github.com/zbloss/dgx-llm-server/issues/30)'s research time (2026-09-16, same day — the page was evidently published between that research and now).

The verified recipe uses `nvidia/Qwen3.8-Flash-Next-NVFP4`, not `RadixArk/Qwen3.8-Flash-Next-NVFP4`, on the same `lmsysorg/sglang:dev-qwen38-next-local` image already chosen, and includes working `--speculative-algorithm NEXTN` sub-flags — exactly the gap ADR 0016 left open ("required sub-flags... not found in any primary source during implementation"). It also carries published benchmarks on this exact image/commit (`dev-qwen38-next-local` @ `4ccff141db`): GSM8K 97.1%, and a full concurrency sweep (TTFT/TPOT/throughput at concurrency 1/4/8).

Verbatim verified command (confirmed live, not from a secondhand paste):

```
sglang serve \
--model-path nvidia/Qwen3.8-Flash-Next-NVFP4 \
--tp 1 \
--moe-runner-backend flashinfer_cutlass \
--fp4-gemm-backend flashinfer_cutlass \
--page-size 64 \
--chunked-prefill-size 4096 \
--context-length 262144 \
--speculative-algorithm NEXTN \
--speculative-num-steps 3 \
--speculative-eagle-topk 1 \
--speculative-num-draft-tokens 4 \
--max-running-requests 8 \
--max-mamba-cache-size 40 \
--reasoning-parser qwen3 \
--mem-fraction-static 0.85 \
--host 0.0.0.0 \
--port 30000 \
--ple-offload-embedding \
--ple-offload-backend file
```

**A second block of text, pasted alongside the above during this session, claiming NVIDIA's checkpoint has MTP experts that "cannot be split across two ranks" and that a "two-node low-latency cell reads the MTP draft from the RadixArk export," could not be found anywhere on the live page** (checked via full page-text extraction, including the "DGX Spark notes" accordion). That claim is not part of this decision's basis and should be treated as unverified/likely fabricated if it resurfaces.

The RadixArk checkpoint's partial download (113GB of ~127GB) was deleted from `/home/zbloss/models` as part of clearing the hung state — no data loss concern, since it was never a complete, servable checkpoint.

## Decision

- Replace `RadixArk/Qwen3.8-Flash-Next-NVFP4` with `nvidia/Qwen3.8-Flash-Next-NVFP4` in `models/models.json`.
- Rewrite `compose.yaml`'s `sglang serve` command to match the verified recipe, keeping this repo's existing infra overrides:
  - **Kept from ADR 0016** (not part of the verified recipe, still required for this repo's setup): `--host 0.0.0.0 --port 8000` (Traefik routing expects 8000, not the recipe's default 30000), `--served-model-name qwen3.8-flash-next`, `--ple-offload-dir /ple-cache`, `--tool-call-parser auto` (not in the verified recipe at all — kept for this stack's agentic tool-calling use case since `auto` doesn't depend on a named parser-table entry, per ADR 0016's own research), `--enable-metrics` (added earlier this same deploy, ADR-untracked but trivial), the `rm -rf /ple-cache/* && exec sglang serve ...` entrypoint wrapper and `start_period: 900s` healthcheck timing (restart-flow behavior, unchanged).
  - **Added from the verified recipe**: `--moe-runner-backend flashinfer_cutlass`, `--fp4-gemm-backend flashinfer_cutlass`, `--page-size 64`, `--chunked-prefill-size 4096`, `--context-length 262144`, `--speculative-algorithm NEXTN --speculative-num-steps 3 --speculative-eagle-topk 1 --speculative-num-draft-tokens 4`, `--max-mamba-cache-size 40`.
  - **Changed**: `--reasoning-parser` from `auto` (ADR 0016) to `qwen3` (verified recipe's explicit value — matched exactly rather than left on `auto`, since the SGLang team's own tested config for this checkpoint/image pair is the lower-risk choice here).
- This resolves map [#28](https://github.com/zbloss/dgx-llm-server/issues/28)'s "Not yet specified" item on NEXTN sub-flags — speculative decoding ships in this deploy after all, reversing ADR 0016's "ships without it" call.

## Consequences

- **Supersedes ticket #30's RadixArk recommendation.** That research was sound given what existed at the time (no Flash-Next-specific SGLang cookbook page); it's superseded by a same-day upstream publication, not by a flaw in the original research.
- **Speculative decoding is now enabled on the first real deploy**, not deferred as ADR 0016 planned. Concurrency ceiling remains 8 (`--max-running-requests 8`, matching the verified recipe, same as ADR 0016's prior value).
- **New unverified-in-this-repo flags**: `--moe-runner-backend`, `--fp4-gemm-backend`, `--page-size`, `--chunked-prefill-size`, `--context-length`, `--max-mamba-cache-size` are all new to this stack. They come from a **Verified**-badged upstream recipe, not a guess, but this repo has not yet booted them itself — watch the first boot closely (same caution ADR 0016 gave for its own flag set).
- **Reasoning parser changed from `auto` to `qwen3`** — if `qwen3`'s detector doesn't actually match this architecture's chat template despite the upstream recipe using it, this is a plausible first failure point to check.
- Checkpoint provenance shifts from RadixArk's caveats (ADR 0016 §Checkpoint) to NVIDIA's official card — presumably lower provenance risk, though NVIDIA's card was already known at ticket #30 time and wasn't otherwise deficient, just previously judged a worse *fit* for the offload recipe. That fit concern is now moot: the SGLang team's own verified recipe uses NVIDIA's checkpoint directly, so whatever loading mechanism concerned ticket #30 (file-layout separation for `--ple-offload-backend file`) evidently isn't a blocker in practice.
- `CONTEXT.md` updated alongside this ADR, per repo convention.
