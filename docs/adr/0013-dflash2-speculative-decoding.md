# ADR 0013: Switch speculative decoding from MTP to DFlash2

**Status:** Accepted — unverified pending deploy
**Date:** 2026-09-04
**Amends:** ADR 0010/0012 (same model/image family, speculative-decoding method and image pin updated)

## Context

Following ADR 0012's research into third-party throughput claims (a community repo claiming 60-79 tok/s on SGLang, and a more rigorous comparison at `morethanamachine.com` measuring SGLang/vLLM/llama.cpp head-to-head), the operator asked whether to switch the whole serving stack to SGLang to chase those numbers. That was rejected — see the "considered, not adopted" reasoning below — but the same research surfaced `z-lab/Qwen3.8-27B-DFlash2`, a block-diffusion speculative-decoding draft model for our exact target checkpoint (`Qwen/Qwen3.8-27B`) that its own model card documents as directly supported by **vLLM**, not just SGLang. This ADR evaluates and adopts that path instead: same backend, same GitOps flow, different speculative-decoding method.

### Why not switch to SGLang (considered, not adopted)

- The `morethanamachine.com` comparison (50 seeded HumanEval+ tasks, disclosed methodology, fresh-boot reproducibility check) measured **SGLang+DFlash2 at 36.59 tok/s weighted vs. vLLM+DFlash2 at 32.01 tok/s** on coding tasks — a real but modest ~14% edge, not the 2-3x implied by headline numbers from a less rigorous single-machine repo (which itself disclaimed: "treat absolute numbers as indicative," not durable).
- SGLang's own tracking issue for this hardware ([sgl-project/sglang#11658](https://github.com/sgl-project/sglang/issues/11658)) states GB10/sm_121a support is **not upstream** — only via a custom developer branch, "not recommended for production," with FP8 CUTLASS kernels failing to dispatch and a ~2-week-stale rebase risk.
- The `morethanamachine.com` author independently hit **a full DGX Spark hard-reboot** running DFlash2, requiring an in-place quantized-heads workaround, and found only 2 of 10 published community recipes for this exact model/hardware pair actually ran successfully.
- Moving backends trades a known-working, upstream-official, GitOps-deployed service for an unofficial patch stack, for a double-digit-percent gain, on the workload (long-lived agentic coding sessions) most exposed if the service crashes mid-session. Rejected.

### Why DFlash2-on-vLLM is different

`z-lab/Qwen3.8-27B-DFlash2`'s own model card (a first-party source, not a third-party blog) documents native vLLM support:

```
vllm serve Qwen/Qwen3.8-27B \
  --speculative-config '{
    "method": "dflash",
    "model": "incoai/Qwen3.8-27B-DFlash2",
    "num_speculative_tokens": 7
  }'
```

This card's quick-start was written while DFlash2 support in vLLM was still an unmerged PR (`vllm-project/vllm#52816`), so it originally required building vLLM from that PR branch by hand. Verified before adopting:

- **`vllm-project/vllm#52816`** ("[Spec Decode] DFlash2: local convolution + candidate selector") **merged 2026-08-21** (`gh pr view 52816` → `state: MERGED`, `mergedAt: 2026-08-21T05:27:22Z`). The base plumbing, `vllm-project/vllm#38300` ("Add DFlash speculators config parsing"), merged earlier, 2026-04-15. Both are on `vllm-project/vllm` main.
- Our currently-pinned image (`ghcr.io/spark-arena/dgx-vllm-eugr-nightly@sha256:d2eb44d303ba2888d3a029a63fbca9e33ee87e9338a8294cc3ac611b695cf76c`, tag `2026081501`) was built **2026-08-15** from vLLM commit `5cecfc0...` — six days *before* the DFlash2 merge. It does not have this method.
- Checked `spark-arena/dgx-vllm`'s `build-index.json` (a permanent, digest-keyed mirror of `eugr/spark-vllm`'s nightly builds, chosen specifically to avoid the `:latest`-tag flashinfer-missing bug tracked in `spark-arena/sparkrun#164`) for the newest same-variant (`nightly`, not `nightly-b12x`/`nightly-tf5`) snapshot: **tag `2026090302`**, digest `sha256:a8f5362e73406dd35d1f513db0f2cba050e9075bf5a1e108a9e4337df6e080d8`, built **2026-09-03** from vLLM commit `4cc0cb6f76a608622a29d9ea8f4d415697e21364` (`vllm_version: 0.28.1rc1.dev345+g4cc0cb6f7.d20260903`), with a populated `flashinfer_hash` (unlike the broken `:latest` rebuild path).
- Confirmed via `gh api repos/vllm-project/vllm/compare/<dflash2-merge-commit>...<target-build-commit>` that the DFlash2 merge commit (`b389ac29...`) is an ancestor of the Sept 3 build commit (`behind_by: 0`) — the target image genuinely contains DFlash2 support, not just a plausible date.

### Throughput data (first-party, from the model card's own published eval)

Concurrency-1, 7 draft tokens/step, vs. Qwen3.8's own built-in 7-token MTP, on an H200 (not our hardware — directional only):

| Task | MTP | DFlash 2 |
| :--- | ---: | ---: |
| GSM8K | 178.5 tok/s (2.59x) | **236.1 tok/s (3.43x)** |
| HumanEval | 151.9 tok/s (2.20x) | **214.6 tok/s (3.11x)** |
| MBPP | 153.1 tok/s (2.22x) | **226.9 tok/s (3.29x)** |

Acceptance length (tokens accepted per verification step, higher = less wasted draft compute) is also higher across every task (e.g. HumanEval: MTP 3.91 vs. DFlash2 4.39). These are H200 numbers from the model's authors, not GB10 numbers — the real ceiling on our hardware is whatever `scripts/benchmark.py` measures after deploy, not this table. Treated as directional evidence the method itself is a genuine improvement over MTP, not as an expected absolute tok/s figure here.

## Decision

- **Bump the pinned image** to `ghcr.io/spark-arena/dgx-vllm-eugr-nightly@sha256:a8f5362e73406dd35d1f513db0f2cba050e9075bf5a1e108a9e4337df6e080d8` (tag `2026090302`), the oldest verified-DFlash2-capable same-variant snapshot available. This is a ~3-week jump in vLLM commits (Aug 15 → Sept 3) beyond just the DFlash2 feature — treat the whole deploy as unverified, not just the new flag.
- **Add `z-lab/Qwen3.8-27B-DFlash2`** to `models/models.json` so the GitOps sync downloads it to `/models/z-lab--Qwen3.8-27B-DFlash2` (no `allow_patterns` filter — single ~1.9B-parameter checkpoint, no multi-quant variants).
- **Replace `--speculative-config`**: `{"method":"mtp","num_speculative_tokens":5}` → `{"method":"dflash","model":"/models/z-lab--Qwen3.8-27B-DFlash2","num_speculative_tokens":7}`. `num_speculative_tokens: 7` matches the model card's own benchmarked configuration (block size 8), not a third-party guess.
- The bundled `model_mtp.safetensors` in the target checkpoint is no longer referenced — left in place (harmless, part of the existing repo download), not actively used now that `method` is `dflash` instead of `mtp`.

## Consequences

- **This is a bigger single deploy than any prior ADR in this repo** (new speculative method + new draft-model dependency + a 3-week image jump, all at once) — if the container fails to start or degrades, first check `docker compose logs` for which piece failed (unrecognized `dflash` method on this build, the draft model failing to load, or an unrelated regression from the intervening ~3 weeks of vLLM commits) before assuming it's the speculative-decoding change specifically. Rollback is a two-line revert (image digest + `--speculative-config` line) back to the ADR-0012 state.
- **Standing risk, now directly relevant instead of hypothetical:** two independent third-party reports (one on vLLM+MTP from ADR-0012's research, one on SGLang+DFlash2 from this ADR's research) describe speculative decoding causing a full DGX Spark host hard-reboot under concurrent load at large context, on this same model family. We are now running a different speculative method than either report, on our own image build, but the failure class (aggressive speculative decoding + GB10 + long context) is not backend-specific evidence it's fixed. Watch for unexplained host reboots after this deploy; if one occurs, the fastest diagnostic step is dropping `--speculative-config` entirely to rule it in or out.
- **First KV-cache-capacity re-check needed.** DFlash2's draft model adds its own memory footprint (~1.9B params) on top of the 27B target model and the existing KV cache budget ADR-0011 tuned at `gpu-memory-utilization 0.85`. Watch startup logs for OOM or a smaller-than-expected KV block count; ADR-0011's fallback (drop to `0.82`) applies here too if headroom is tighter than before.
- Once deployed cleanly, run `uv run scripts/benchmark.py` and compare against the ADR-0011 baseline (~31 tok/s) and the rejected-SGLang comparison's vLLM+DFlash2 number (32.01 tok/s weighted) — that's the actual bar this change needs to clear on our hardware, not the H200 table above.
- `CONTEXT.md`'s `Qwen3.8-27B NVFP4` glossary entry (currently describes MTP as the speculative method) needs updating to describe DFlash2 instead — done alongside this ADR.
