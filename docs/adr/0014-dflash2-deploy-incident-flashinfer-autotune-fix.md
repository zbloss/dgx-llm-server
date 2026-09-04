# ADR 0014: DFlash2 deploy incident — root cause and fix for the FlashInfer autotune OOM/hang

**Status:** Accepted — verified on hardware
**Date:** 2026-09-04
**Amends:** ADR 0013 (resolves its "unverified pending deploy" status)

## Context

ADR 0013's DFlash2 deploy (`f9e356d`) went out to the DGX Spark and crashed the host three times in about 75 minutes:

1. **Full host hard-reboot.** During the FlashInfer `bf16_fp4_cute_dsl_gemm` autotune step (23 kernel-config profiles, `--enable-flashinfer-autotune`), per-profile time ballooned from ~1s to 38s average as memory pressure built. `NVRM: Out of memory [NV_ERR_NO_MEMORY]` fired three times, the engine core hung (`shm_broadcast: No available shared memory broadcast block found in 60 seconds`, repeating every 60s for 8+ minutes), Linux's oom-killer fired, and the host hard-rebooted with no clean recovery logged.
2. **System-wide OOM-kill rampage, caught before a second reboot.** On restart, the same container hit the identical hang at the identical step. Manually stopping it (`docker compose stop`) forced a delayed SIGKILL that, on release, triggered a burst of `Out of memory: Killed process` events against unrelated desktop-session processes (`pipewire`, `gnome-shell`, `bluetoothd`, etc.) — collateral of freeing a large chunk of GB10's unified CPU/GPU memory pool all at once. Host stayed up this time.
3. **Near-miss, caught at 862Mi free.** Lowering `--max-num-batched-tokens` from 32768 to 8192 (hypothesis: the autotuner's profiling batch is sized off this value, per its own log line `Running FlashInfer autotune with N tokens`) did not prevent the hang — it recurred at the identical step, and system memory dropped to 862Mi free / 4.7Gi swapped before a manual stop caught it.

Between incidents, dropping `--enable-flashinfer-autotune` entirely (rather than lowering batch size) was also tried and *also* failed the same way — autotuning ran anyway, confirmed in logs (`[AutoTuner]: Tuning bf16_fp4_cute_dsl_gemm`, two full passes back to back).

## Root cause

Read directly from `vllm-project/vllm` source at the pinned commit (`4cc0cb6f7...`):

- `vllm/config/kernel.py`: `KernelConfig.enable_flashinfer_autotune: bool = None` — the field's default is `None`, not `False`.
- `vllm/model_executor/warmup/kernel_warmup.py`: the gate is
  ```python
  if enable_flashinfer_autotune is False:
      logger.info_once("Skipping FlashInfer autotune because it is disabled.")
  elif has_flashinfer() and current_platform.has_device_capability(90):
      flashinfer_autotune(worker.model_runner)
  ```
  This checks `is False` specifically. `None is False` evaluates to `False`, so the `elif` branch runs and autotuning proceeds — **omitting the CLI flag does not disable it.** The batch-size change was irrelevant; it just resized (and slightly reduced the peak of) the same crash.
- The real disable path is a dotted nested-config CLI override, documented in vLLM's own `docs/design/optimization_levels.md` as part of their `-O0` bundle: `--kernel-config.enable_flashinfer_autotune=False`. This explicitly sets the field to `False`, which the `is False` check actually matches.

Applying `--kernel-config.enable_flashinfer_autotune=False` was confirmed active in the engine's own resolved config dump at startup (`KernelConfig(... enable_flashinfer_autotune=False ...)`) and produced the log line `Skipping FlashInfer autotune because it is disabled.` — the crash step no longer runs at all.

**DFlash2 itself was never the problem.** A tangent investigated mid-incident — a community report that DFlash2 requires an unquantized target LM head, and that both 4-bit checkpoints (including NVFP4) fail a hard `ValueError` check — turned out not to apply here: `unsloth/Qwen3.8-27B-NVFP4`'s `config.json` shows a mixed-precision scheme where `lm_head` is quantized at 8-bit (int8, `group_0`), not 4-bit — only `mlp.(gate|up|down)_proj` layers get true 4-bit NVFP4. None of our three crashes ever raised that ValueError, consistent with our checkpoint not tripping that specific check.

## Decision

- `compose.yaml`: `--max-num-batched-tokens` set to `8192` (down from `32768`) and `--enable-flashinfer-autotune` replaced with `--kernel-config.enable_flashinfer_autotune=False`.
- Both changes are kept, not just the second: the batch-size reduction wasn't the fix, but there's no evidence it hurt, and it stays as a lower-risk default while `enable_flashinfer_autotune` remains a known footgun. It can be revisited (raised back toward 32768) as an isolated, deliberate test later — not during an incident.
- No further changes to the DFlash2 speculative-decoding config itself (`num_speculative_tokens: 7`, model path) — it was never implicated.

## Verification

Post-fix, `uv run scripts/benchmark.py` against the Spark:

| Prompt | TTFT | Completion tok/s | Note |
|---|---|---|---|
| no_think | 0.19s | 100.0 tok/s | small sample (32 tokens) |
| **medium_effort** | 0.32s | **50.1 tok/s** (307 real answer tokens) | the load-bearing data point |
| xhigh_effort | 0.28s | — | hit the 1024-token budget entirely in reasoning; 0 answer tokens, the reported "100.0 tok/s" on this row is not a real measurement |

`medium_effort`'s 50.1 tok/s clears both reference points cited in ADR 0013: the pre-DFlash2 ADR-0011 baseline (~31 tok/s) and the rejected-SGLang-comparison's own measured vLLM+DFlash2 figure (32.01 tok/s). DFlash2 does deliver the throughput gain ADR 0013 predicted — the entire blocker was the autotune-gate bug above, unrelated to DFlash2 or NVFP4.

## Consequences

- **`--enable-flashinfer-autotune` (from ADR 0012) is now known-broken as a disable mechanism** — it can only ever turn autotuning *on* (explicitly `True`) or leave it at the *effectively-on* default (`None`). If autotune is ever wanted again, use `--kernel-config.enable_flashinfer_autotune=True`; if disabling it is ever needed again, only `--kernel-config.enable_flashinfer_autotune=False` actually works. Do not trust omitting the flag.
- **The underlying autotune-triggered memory pressure on this GB10 unified-memory system is still real and undiagnosed as to *why* it's so severe** — we've disabled the trigger, not root-caused the memory blowup itself. If a future vLLM image bump silently re-enables autotune by default, or if `flashinfer_autotune()`'s behavior changes, this class of crash could recur without an obvious CLI-level cause. Anyone touching this image or these flags should re-verify `Skipping FlashInfer autotune because it is disabled.` appears in fresh logs before trusting a deploy.
- This push (`compose.yaml` + this ADR) will trigger the standard GitOps sync-and-restart on the Spark. Since it converges to the exact configuration already verified healthy live via SSH tonight, risk is low, but it is a full container restart of a currently-stable service and was watched through completion at deploy time.
