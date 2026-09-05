# ADR 0015: Revert DFlash2 to MTP pending upstream prefix-cache fix

**Status:** Accepted
**Date:** 2026-09-04
**Amends:** ADR 0013/0014 (reverses the DFlash2 switch; MTP is restored to the exact ADR-0011 configuration)

## Context

ADR 0013/0014 covered adopting DFlash2 speculative decoding and fixing the FlashInfer-autotune incident it triggered. Once the service was stable, further investigation turned up a second, independent problem: DFlash2 gives zero prefix-cache benefit on this model's hybrid GDN (Gated DeltaNet) architecture.

### Finding: DFlash2 defeats prefix caching entirely

Using guidellm (see `guidellm/`) to model realistic multi-turn agentic-coding traffic (a large shared context re-sent every turn, growing conversation history — the actual shape of Claude Code / Qwen Code / Hermes traffic, not generic short prompts), and a direct `curl` + `/metrics` probe sending an identical large prompt twice:

| Config | Image | `prefix_cache_hits_total` on exact repeat | Cold → warm request time |
|---|---|---|---|
| DFlash2 (current pin at the time) | 2026-09-03 build | **0%** | 7.20s → 7.15s (no change) |
| No speculative decoding | 2026-09-03 build | 71% | 8.97s → 2.06s |
| MTP (the ADR-0011 config, 3-week-stable) | 2026-08-15 build | 36.5% | 8.60s → 1.81s |
| DFlash2 on the oldest possible build | 2026-08-21 build (8hrs post-merge) | **0%** | 2.45s → 2.39s (no change) |

The last row rules out "wait for a newer image" as a fix: DFlash2 has never had working prefix-cache integration on this architecture, from the very first vLLM build that supported it. This is corroborated by upstream reports (`vllm-project/vllm#54360`, `#53670`, `#53477` among others) and root-caused in an open, unmerged fix PR (`#52244`): the cache producer publishes hybrid-GDN state at a boundary the speculative-decode consumer's draft-token "rewind" can't align to. MTP hits the same mechanism, but MTP has *some* history of working (confirmed correctly on v0.24.0) and degrades rather than fails outright — hence the nonzero 36.5% on the older MTP build above.

### Why this matters for this deployment specifically

This server's traffic is agentic coding (Claude Code, Qwen Code CLI) and agentic use via the Hermes deployment — nearly all multi-turn, sending a large, mostly-repeated context (system prompt, tool schemas, accumulating history) on every turn. Modeling a realistic session with tonight's measured rates (DFlash2: 48 tok/s decode, faster cold-prefill; MTP/no-spec baseline: 31 tok/s decode, but real cache reuse from turn 2 onward):

| Turns | DFlash2 total | No-spec-decode total |
|---|---|---|
| 6 | 112.1s | 105.5s |
| 12 | 271.0s | 221.9s |
| 20 | 555.9s | 403.6s |

DFlash2 wins on any single isolated request (nothing to cache yet either way) and up to about turn 4. Past that — which is most real agentic-coding sessions — the lack of caching makes DFlash2 progressively slower as context accumulates, not faster.

## Decision

Revert to the exact ADR-0011 configuration: image digest back to `sha256:d2eb44d303ba2888d3a029a63fbca9e33ee87e9338a8294cc3ac611b695cf76c` (tag `2026081501`), `--speculative-config '{"method":"mtp","num_speculative_tokens":5}'`, no FlashInfer-autotune flags (this image predates that work entirely and ran stably without them for three weeks). This is a full byte-for-byte revert to the pre-ADR-0013 `compose.yaml`, verified live before committing.

MTP still only gets partial caching (36.5%, not the ~71%+ a fully-working cache should give), but it beats DFlash2's 0% and is the best currently-available tradeoff between decode speed and prefix-cache reuse for this workload.

**Not adopted, and why:** no speculative decoding at all, despite the highest measured cache-hit rate (71%). MTP's decode speedup over the no-spec baseline is real and still worth keeping; the partial cache loss is a real but smaller cost than losing decode speed on every turn, including the many short ones.

## Revisit condition

Re-evaluate DFlash2 once `vllm-project/vllm#52244` ("Restore hybrid GDN prefix-cache hits under MTP spec decoding") or an equivalent fix merges upstream. That PR targets the exact mechanism identified here (the composed hash-unit misalignment between the cache producer and speculative-decode consumer); if it lands, both MTP and DFlash2 would be expected to see materially better cache-hit rates, and DFlash2's decode-speed advantage would no longer come with the current tradeoff. Check the PR's merge status and, if merged, re-run the guidellm + curl/metrics probe in this ADR before switching anything.

## Consequences

- Deployed config and `compose.yaml` are now byte-identical (verified via diff) to the state that ran stably for three weeks before ADR-0013. This is the lowest-risk state available.
- `models/models.json` still lists `z-lab/Qwen3.8-27B-DFlash2` as a downloaded model; it's simply unused while `method: mtp` is active (same as `model_mtp.safetensors` being unused was under DFlash2). Left in place rather than removed, since ADR 0013's adoption work and this revisit condition both assume it'll be needed again.
- `guidellm/` (the Dockerized benchmarking tool built during this investigation) stays as permanent repo tooling — it's what made this entire investigation possible and is the right way to re-test the revisit condition above.
