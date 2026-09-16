# ADR 0018: Host-side memory watchdog for unified-memory exhaustion

**Status:** Accepted — unverified pending deploy
**Date:** 2026-09-16
**Amends:** ADR 0016 (closes the memory-watchdog gap flagged there but never built); resolves wayfinder map [#28](https://github.com/zbloss/dgx-llm-server/issues/28) ticket [#38](https://github.com/zbloss/dgx-llm-server/issues/38)

## Context

SGLang's verified DGX Spark cookbook recipe (`docs.sglang.io`, "DGX Spark notes" — the accordion this repo's research missed expanding at both ADR 0016 and ADR 0017's research time, and again briefly during ticket #33's verification, until properly re-checked while resolving this ticket) recommends:

> a host-side watchdog that kills the server when `MemAvailable` drops below a few GiB, because a unified-memory exhaustion can take the whole box down and needs a power cycle to recover.

This is the same failure class ADR 0012/0014 already hit once on this host, via a different mechanism (FlashInfer autotune leaving `enable_flashinfer_autotune` effectively on, OOMing/hanging the box three times). ADR 0016 flagged the missing watchdog as an accepted gap for the first `qwen3.8-flash-next` deploy; it was never built. `docs.sglang.io`'s own recipe doesn't ship a ready script or an exact threshold — "a few GiB" is the only guidance given.

### Why now, and why this shape

Ticket #33's deploy (ADR 0017) landed the checkpoint switch and NEXTN speculative decoding — the deploy this watchdog was deferred from — and came up healthy with no exhaustion incident. But nothing in this stack currently guards against it happening on a future restart, model-swap, or a request pattern that pushes past `--mem-fraction-static 0.85`'s reserved headroom.

Deployment shape follows this repo's own precedent from ADR 0016's PLE-restart-flow decision: **everything ships through `compose.yaml`, because `sync-models.yml` only ever copies that one file to the host** (`cp $GITHUB_WORKSPACE/compose.yaml .` — see `.github/workflows/sync-models.yml`), not a build context or arbitrary script files. A separate `scripts/mem_watchdog.sh` synced by a new CI step was considered and rejected for the same reason ADR 0016 rejected a custom Dockerfile for the PLE-delete wrapper: it adds a second distribution mechanism for one small piece of logic, when an inline `entrypoint: ["sh", "-c"]` command (the pattern `sglang-server` itself already uses) does the job with nothing new to keep in sync.

### Alternatives considered

- **Host systemd unit/timer.** Rejected: would require manual provisioning on the DGX Spark host outside this repo's GitOps flow (no existing precedent here for host-level, non-Docker artifacts), and drifts silently if the host is ever rebuilt.
- **`pid: host` + signal the `sglang` process directly**, instead of `docker kill` over the Docker socket. Rejected: broader access (full host PID namespace) for no real benefit — killing the container's PID 1 (which `exec sglang serve` makes the actual `sglang` process, since `compose.yaml`'s entrypoint wrapper `exec`s into it) already triggers the same `restart: unless-stopped` + PLE-cache-clearing recovery path a `docker kill` does, more simply.
- **A tighter or looser threshold than "a few GiB."** The single-Spark recipe's own DGX Spark notes cite `--mem-fraction-static 0.85` leaving "~12–18 GB for the pools" at steady state; picking a kill threshold well below that floor, with margin before genuine exhaustion, matches the "a few GiB" language most directly. See Decision.

## Decision

Add a `mem-watchdog` sidecar service to `compose.yaml`:

- **Image:** `docker:27-cli` (official, multi-arch, includes the `docker` CLI and a busybox shell/awk — nothing else needed).
- **Mounts:** `/proc:/host/proc:ro` (system-wide `MemAvailable`, no `pid: host` needed — `/proc/meminfo` isn't process-namespaced) and `/var/run/docker.sock:/var/run/docker.sock` (read-write — a `:ro` bind can block the write side of the socket connection `docker kill` needs, unlike a purely read-only inspect/list use case).
- **Logic:** poll `/host/proc/meminfo`'s `MemAvailable` every `POLL_INTERVAL_SECONDS` (10s); if it drops below `MEM_AVAILABLE_MIN_KB` (4 GiB — `4194304` KB, chosen as "a few GiB" with margin below the recipe's ~12–18GB normal-operation floor and above zero), `docker kill` the target container (`dgx-llm-server-sglang-server-1`, this compose project's name for the `sglang-server` service) and log a UTC-timestamped line to stdout (captured by `docker logs`, same as every other service here — no new log destination). After a kill, sleep `COOLDOWN_SECONDS` (120s) before resuming polling, so the container has time to restart and reclaim memory before the watchdog re-evaluates.
- **Recovery:** `docker kill` (not `stop`/`rm`) lets `sglang-server`'s existing `restart: unless-stopped` policy and its own entrypoint wrapper (`rm -rf /ple-cache/* && exec sglang serve ...`) bring it back with a freshly-rebuilt PLE table — the identical recovery path any other crash already takes (ADR 0016's restart-flow), so this adds no new restart behavior to reason about, just a new trigger for the existing one.
- Dry-run tested locally (not on-host): the poll/threshold/log logic against a mocked `/proc/meminfo`, both the below-threshold (fires, logs, attempts kill) and healthy (stays quiet) cases.

## Consequences

- **The Docker socket mount is root-equivalent host access**, same class of risk this compose file already accepts for GPU passthrough (`deploy.resources.reservations.devices`) and the PLE-cache entrypoint wrapper — acceptable for this single-operator homelab box, not something to replicate on a shared or multi-tenant host without hardening (e.g., a socket proxy scoped to just `kill`).
- **Unverified in production**: dry-run tested against a mocked `/proc/meminfo`, not against a real exhaustion event on the DGX Spark. First real trigger (if any) should be checked closely — does the kill actually land before the box hangs, does the restart genuinely recover cleanly.
- **4 GiB threshold is a judgment call**, not a measured line. If it fires spuriously during normal load (e.g., a long-context request transiently pushing `MemAvailable` down without genuine runaway growth), raise it or extend `COOLDOWN_SECONDS`; if a real exhaustion event still hangs the box before the watchdog polls, lower `POLL_INTERVAL_SECONDS` or raise the threshold further.
- Resolves map #28's "Not yet specified" watchdog item and closes ticket #38.
- `CONTEXT.md` updated alongside this ADR, per repo convention.
