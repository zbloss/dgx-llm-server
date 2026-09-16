# ADR 0016: Replace qwen3.8-27b with Qwen3.8-Flash-Next via SGLang's single-GB10 NVMe-PLE recipe

**Status:** Accepted — unverified pending deploy
**Date:** 2026-09-16
**Amends:** ADR 0010 (replaces the served model and the serving backend); revisits ADR 0013's rejection of a full SGLang migration, now scoped narrowly to this one model

## Context

Qwen3.8-Flash-Next is a preview-of-Qwen4-architecture model (176B total / 6B active GDN+QSA hybrid MoE) with an SGLang-documented recipe for running on a single DGX Spark by offloading its PLE ("n-gram" positional lookup embedding) table to local NVMe instead of holding it resident in unified memory. Planned and researched via `wayfinder` map #28; see that issue's Decisions-so-far and linked tickets for the full trail. This ADR is the map's architecture-tradeoff ticket ([#31](https://github.com/zbloss/dgx-llm-server/issues/31)), resolved.

### Why this forces a full replacement, not an addition

Qwen3.8-Flash-Next's weights total ~133GB (≈79GB main shards + ≈48GB PLE-table-equivalent), which exceeds the Spark's 128GB unified memory on their own, before KV cache. There is no configuration in which it coexists with the currently-served `qwen3.8-27b` (~16GB NVFP4). The NVMe offload is therefore load-bearing — required just to fit at all, not a performance optimization — and `qwen3.8-27b` must come off this host for the new model to run.

### Re-checking ADR 0013's SGLang rejection

ADR 0013 rejected migrating the whole stack to SGLang, citing `sgl-project/sglang#11658`: GB10/sm_121a support "not upstream, dev-branch only, not recommended for production." Re-verified 2026-09-16, 12 days later: still true, and more specific than before.

- The single-Spark recipe depends on three PRs (`#38121` ModelOpt mixed-precision loader, `#37068` file-backed PLE table backend, `#38308`/`#38290` GB10 router-kernel fixes), all merged only into a branch called `qwen4-main-squashed` — not `main`.
- The PR that would merge that branch to `main`, [`sgl-project/sglang#36497`](https://github.com/sgl-project/sglang/pull/36497) ("Introduce Qwen 3.8 Flash Next"), is **closed, not merged**. The branch is 1,595 commits behind `main`, 11 ahead.
- The standard release image (`qwen38flashnext`) predates all three fixes and cannot load this checkpoint. The only working path is `lmsysorg/sglang:dev-qwen38-next-local`, or a self-build from `qwen4-main-squashed` at commit `4ccff141db`.
- `sgl-project/sglang#11658` itself closed in January via an inactivity bot, not resolution. A live, still-open issue ([`#39497`](https://github.com/sgl-project/sglang/issues/39497), opened 2026-09-15) shows another operator hitting a `qwen4_exp` architecture-not-recognized error on the standard images — confirming the dev-branch dependency is current, not stale research.

This is the same shape of risk ADR 0013 weighed for DFlash2 (real, working, first-party-adjacent capability, confined to an unofficial branch) — not the "personal fork, unrecommended for production" shape that ADR 0013 rejected outright for a whole-stack migration. Scoped to this one model, on an operator-driven homelab box, for an exploratory deploy, the operator has chosen to accept it (see map #28, "Risk re-check" round).

### Checkpoint

Resolved via [ticket #30](https://github.com/zbloss/dgx-llm-server/issues/30): `RadixArk/Qwen3.8-Flash-Next-NVFP4`, not NVIDIA's official `nvidia/Qwen3.8-Flash-Next-NVFP4` quant. NVIDIA's card scopes support to vLLM only / B200-B300 only, and bundles the PLE table together with MTP weights in one monolithic file that doesn't cleanly split for `--ple-offload-backend file`. RadixArk's repo isolates the PLE table into its own ~47.7GiB shard set matching the offload recipe, and explicitly targets SGLang. Caveats (self-labeled "private candidate release," no GB10-specific validation from either publisher) are carried forward as accepted risk — see `docs/research/qwen3.8-flash-next-checkpoint.md`.

### Restart-flow behavior for the PLE-table rebuild

SGLang fully repopulates the ~47.7GiB PLE table on every process start, regardless of whether a valid one already exists. The speed depends on the on-disk file's state: a fresh, empty sparse file populates at GB/s (~10min); an already-allocated file being overwritten in place is punch-hole-rewritten at ~17MB/s (~55min). There is no "skip rebuild" path — only control over which of those two speeds a given restart gets.

Decided (map #28, "restart-flow" round):

- `sync-models.yml` deletes the stale PLE file immediately before `docker compose up -d --remove-orphans`, guaranteeing the fast (~10min) path on every CI-triggered restart.
- Additionally, `compose.yaml` overrides the container's `entrypoint` to `["sh", "-c"]` and wraps the launch command as `rm -rf /ple-cache/* && exec sglang serve ...`, so the delete happens on **every** process start — not just CI-triggered ones — regardless of trigger (host reboot, OOM-kill, docker daemon restart, `restart: unless-stopped` crash-loop recovery). Implemented this way instead of the custom-Dockerfile approach originally sketched during grilling: a compose-level entrypoint override achieves the identical guarantee without a new image to build or a build-context-distribution problem (the self-hosted runner's `sync-models.yml` only ever copies `compose.yaml` itself, not a build context). The two mechanisms are redundant by design (belt-and-suspenders): the workflow-level delete keeps the cleanup visible in CI logs even though the compose-level wrapper alone is sufficient.
- `compose.yaml`'s healthcheck `start_period` moves from `240s` to `900s` (15min) — comfortably covers the ~10min fast path this setup now guarantees on every restart, without the hour-long flat timeout that would delay flagging a genuinely broken container.

### Capacity and runner access

Resolved via [ticket #29](https://github.com/zbloss/dgx-llm-server/issues/29): no pre-verification gate. The operator's call is to download what's needed and clear space reactively (old model directories, unused Docker images) if the runner hits capacity or pull issues — handled as part of the deploy ticket ([#33](https://github.com/zbloss/dgx-llm-server/issues/33)), not a separate upfront check.

## Decision

- Replace `qwen3.8-27b` entirely. Remove `unsloth/Qwen3.8-27B-NVFP4` and `z-lab/Qwen3.8-27B-DFlash2` from `models/models.json`; add `RadixArk/Qwen3.8-Flash-Next-NVFP4` as `qwen3.8-flash-next`.
- Replace the `vllm-server` service in `compose.yaml` with an `sglang-server` service, image `lmsysorg/sglang:dev-qwen38-next-local`, launched via `sglang serve` (SGLang's own `vllm serve`-equivalent CLI). Flags: `--model-path /models/RadixArk--Qwen3.8-Flash-Next-NVFP4 --served-model-name qwen3.8-flash-next --host 0.0.0.0 --port 8000 --tp 1 --ple-offload-embedding --ple-offload-backend file --ple-offload-dir /ple-cache --mem-fraction-static 0.85 --max-running-requests 8 --reasoning-parser auto --tool-call-parser auto`. Healthcheck `start_period: 900s`.
  - **Port and host are explicit, not defaults**: SGLang's `--port`/`--host` default to `30000`/`127.0.0.1`; `8000`/`0.0.0.0` are set explicitly to keep the existing Traefik routing (`dgx.blosshomelab.com` → port 8000, configured in the separate `home-server` GitOps repo) working unchanged.
  - **Parsers are `auto`, not a hardcoded name**: this repo's vLLM config used explicit `qwen3`/`qwen3_xml` parser names, but SGLang's source (`ReasoningParser.DetectorMap`, `FunctionCallParser.ToolCallParserEnum`) has no `qwen4`-family entry — `auto` (resolves the detector from the chat template at startup, per SGLang's own cookbook doc) is the only verified-working option for this architecture.
  - **No speculative decoding in this first pass.** SGLang's MTP-equivalent is `--speculative-algorithm NEXTN`, but its required sub-flags (draft-token count etc.) were not found in any primary source during implementation — guessing them risks an unbootable container. Ships without it; the deploy ticket's benchmark ([#34](https://github.com/zbloss/dgx-llm-server/issues/34)) will therefore land closer to the recipe's published non-speculative numbers (15.9 tok/s single-stream / 83 tok/s @ 8-concurrent) than the with-NEXTN numbers (27.5 / 71.7) quoted earlier in this ADR. Revisit once NEXTN's exact flags are found.
- `compose.yaml` overrides `entrypoint: ["sh", "-c"]` with `command: rm -rf /ple-cache/* && exec sglang serve ...` (see restart-flow section above) instead of a separate Dockerfile.
- `sync-models.yml` gets a step deleting `/home/zbloss/sglang-ple-cache/*` immediately before `docker compose up -d --remove-orphans`.
- Concurrency ceiling is 8 requests (`--max-running-requests 8`), matching this stack's existing `--max-num-seqs 8` convention from the vLLM config it replaces.
- Full implementation lands in [#32](https://github.com/zbloss/dgx-llm-server/issues/32); deploy and health confirmation in [#33](https://github.com/zbloss/dgx-llm-server/issues/33); benchmark comparison against the stored `qwen3.8-27b` baselines in [#34](https://github.com/zbloss/dgx-llm-server/issues/34).

## Consequences

- **This is a bigger single change than any prior ADR in this repo**: new backend (SGLang, not vLLM), new non-default dev image, new custom entrypoint wrapper, new model architecture, and the serving stack's only model swapped outright, all at once. If the deploy ticket hits trouble, check each piece independently (image pull/build, checkpoint download, PLE-offload flags, entrypoint wrapper) rather than assuming one root cause.
- **Standing risk, explicitly accepted, not resolved:** the recipe depends on a closed, unmerged SGLang PR (`#36497`) and an integration branch (`qwen4-main-squashed`) with no committed upstream timeline. If that branch stalls or the dev image stops being published, this deploy has no supported upgrade path until SGLang either merges the branch or ships an equivalent through a maintained channel. Revisit if `#36497` reopens/merges, or if a newer official image ships GB10 support.
- **Checkpoint provenance risk, explicitly accepted:** `RadixArk/Qwen3.8-Flash-Next-NVFP4` is a community, self-labeled "private candidate release" with no GB10-specific validation from either publisher. It is now the sole load-bearing weight source for this deployment.
- **No pre-verified capacity headroom.** Disk/Docker-pull issues on the runner are handled reactively (delete old artifacts) rather than checked in advance — expect the first deploy attempt to potentially need manual cleanup on the host.
- **Concurrency ceiling (8 requests) is materially lower than DFlash2/MTP-era headroom** on the old stack; if agentic-coding traffic regularly exceeds 8 concurrent requests, this is a real regression to watch for post-deploy.
- **New risk found during implementation, not previously flagged:** the recipe's own cookbook doc recommends a host-side watchdog that kills the server when `MemAvailable` drops below a few GiB, warning that unified-memory exhaustion "can take the whole box down and needs a power cycle to recover" — the same failure class ADR 0012/0014 already hit once on this host via a different mechanism (FlashInfer autotune). No such watchdog exists in this repo. Left as an accepted gap for this first deploy, not built as part of #32 — added to map #28's "Not yet specified" as follow-up work; watch closely during [#33](https://github.com/zbloss/dgx-llm-server/issues/33)'s deploy.
- **Rollback is not a one-line revert** this time, unlike ADR 0011–0015's speculative-decoding-only changes: it's the full `compose.yaml` service definition plus `models/models.json` plus the new `Dockerfile`. Keep the pre-ADR-0016 `compose.yaml`/`models.json` state easy to diff back to (git history) rather than relying on a documented flag-revert.
- Once deployed cleanly, run `guidellm/run.sh` and compare against the stored `qwen3.8-27b` baselines in `guidellm/results/` (~50 tok/s per ADR 0014) across the full concurrency sweep — the recipe's own published numbers (27.5 tok/s single-stream, 71.7 tok/s at 8-concurrent) suggest this loses at low concurrency and wins at the ceiling, so a single-number comparison would be misleading.
- `CONTEXT.md`'s glossary and architecture history need updating to describe SGLang/`qwen3.8-flash-next` as the current stack — done alongside this ADR.
