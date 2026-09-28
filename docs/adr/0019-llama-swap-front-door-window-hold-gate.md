# ADR 0019: llama-swap front door with a Window-hold gate

**Status:** Accepted — deployed and verified on the Spark 2026-09-28 (see Cutover verification); boot-preload retry added after that verification
**Date:** 2026-09-27
**Supersedes:** ADR 0016's "one resident model, no swap-by-name" clause only (its SGLang, checkpoint and PLE-offload decisions stand)
**Amends:** ADR 0018 (mem-watchdog targets containers by label); restates ADR 0003's constraint
**Spec:** [ai-grifting#20](https://github.com/zbloss/ai-grifting/issues/20) (grilling resolution), tracked by [ai-grifting#23](https://github.com/zbloss/ai-grifting/issues/23)

## Context

The ai-grifting content pipeline needs the Spark's GPU for a nightly **Generation window** (ai-grifting's term: the nightly period its batch of ComfyUI + QA work runs in). Flash-Next alone fills the Spark's 128GB of unified memory (ADR 0016), so the two can't coexist. Something has to swap them by request, and during the window LLM clients have to be refused rather than trigger a 10-minute Flash-Next reload that would evict generation.

llama-swap does request-driven swapping with mutually exclusive groups. It has no notion of a time-limited "refuse this group for now" lease, so that part lives in a small sidecar in front of it.

## Decision

**Layout.** Nothing changes for clients: `dgx.blosshomelab.com` → `192.168.68.104:8000` (the `home-server` route is unchanged).

| Port | Binds | Who |
|---|---|---|
| 8000 | `0.0.0.0` | **gate** (`gate/gate.py`) - the front door |
| 8001 | `127.0.0.1` | gate admin API (Window hold) |
| 8080 | `127.0.0.1` | **llama-swap** (v260, `llama-swap/`) |
| 30000 | `127.0.0.1` | `sglang-server` (managed) |
| 8188 / 8189 | `127.0.0.1` | ComfyUI / QA (managed, later) |

All of them use `network_mode: host`.

**llama-swap** runs on `docker:27-cli` with the pinned release binary added (sha256-checked), so it has the docker CLI and compose plugin. It gets the docker socket and the repo mounted read-only at its host path, so `docker compose -p dgx-llm-server -f <host path>/compose.yaml` inside it resolves the project the same way the host does. `-watch-config` reloads `llama-swap/config.yaml` when the workflow copies a new one into place.

**Managed services.** `sglang-server` stays in `compose.yaml` under `profiles: [managed]`, with `restart: "no"` and the label `llama-swap.managed=true`, so a plain `docker compose up` never starts it. llama-swap's `cmd` is an *attached* `docker compose ... up --force-recreate sglang-server`, so the process lives as long as the container. `cmdStop` is `compose stop`. The PLE delete-on-start entrypoint is unchanged. The model id stays `qwen3.8-flash-next`, which every client already sends.

**Restart policies.** The gate, llama-swap and mem-watchdog are `unless-stopped`. Every managed service is `no`.

**Boot and timeouts.** `hooks.on_startup.preload: [qwen3.8-flash-next]`, backed up by the `flash-next-warm` sidecar (see Consequences). `healthCheckTimeout: 3600`. `sendLoadingState: false`, so clients just block through the ~10-minute warm start, as they did after an SGLang restart before. The gate's upstream timeout (3900s) is longer than that.

**Window hold (the gate).** The gate keeps an in-memory lease with a 30-minute TTL.
- Admin API on `127.0.0.1:8001` only:
  - `POST /hold` opens the hold.
  - `PUT /hold` renews it, and also re-opens a hold that a gate restart dropped.
  - `DELETE /hold` releases it.
  - `GET /hold` reports it.
  - Every one of these returns `{"active", "expires_in"}`.
- While a hold is active, the gate lets these through: `/v1/models`, `/models`, `/health`, `/metrics`, `/running`, plus any request whose model (from the JSON body, or `/upstream/<model>/…`) is in `GENERATION_MODELS`. Everything else gets `503` with `Retry-After` set to the seconds left on the lease.
- **Fails closed, a deliberate refinement of the spec's list.** llama-swap v260 starts models from many more routes than the chat and embeddings endpoints, including `/v1/messages`, `/v1/responses`, `/v/*`, `/completion`, `/infill`, `/props`, `/sdapi/*`, `/comfyui/*` and `/upstream/*` (`internal/server/server.go`). An allowlist can't miss one. A request whose model can't be read is refused. The llama-swap UI and `/api` are unavailable externally during a hold; on the Spark, use `127.0.0.1:8080`.
- Owner: the nightly Temporal workflow in ai-grifting.
  - It opens the hold as its first activity and renews it by heartbeat.
  - As its last activity, and on failure, it releases the hold and warms Flash-Next with `GET /upstream/qwen3.8-flash-next/health`.
  - The Temporal worker on the Spark calls llama-swap on `127.0.0.1:8080` directly, so the gate never blocks it.

**Generation group.** ComfyUI and QA become llama-swap models (`ttl: 0`, in a `generation` group exclusive with `llm`) whose `cmd` points at an ai-grifting compose file on the Spark. They are added, together with `GENERATION_MODELS` on the gate, once that file exists. This cutover is LLM-only.

**mem-watchdog (amends ADR 0018).** It now kills every *running* container labelled `llama-swap.managed=true`, not just `dgx-llm-server-sglang-server-1`. The attached `compose up` then exits, llama-swap marks the model stopped, and the next request relaunches it. A Temporal activity interrupted mid-render fails and retries.

**Prometheus (ADR 0003, restated).** Never scrape a path that can start a model.
- The ServiceMonitor scrapes `/upstream/qwen3.8-flash-next/metrics` (SGLang) and llama-swap's own `/metrics`.
- `upstream.ignorePaths: ['^/metrics$']` keeps a scrape from ever starting the model.
- **The spec's open question is resolved:** in v260's `handleUpstream`, an ignored path still proxies to a model that is already Ready, and returns `409` without swapping when it isn't. The LAN-port fallback isn't needed. Scrape gaps while the model is swapped out (and during a hold, when the gate refuses the upstream path) are accepted.

**GitOps (`sync-models.yml`).** The workflow now copies `compose.yaml`, `gate/gate.py` and `llama-swap/*`, and triggers on those paths too. It:
1. Waits (up to 5h, polling every 5 min) while a Window hold is active.
2. Removes any `sglang-server` container that lacks the managed label. This is the one-time cutover step, idempotent after that.
3. Runs `docker compose up -d --build --remove-orphans`, which touches only the gate, llama-swap and mem-watchdog.
4. If `docker compose --profile managed config --hash sglang-server` changed, calls `POST /api/models/unload/qwen3.8-flash-next`, then warms the model through `/upstream/qwen3.8-flash-next/health`.

The hold wait comes first, before model downloads. Because `gate.py` is bind-mounted, compose can't see a change to it, so the workflow restarts the gate when the file differs. On the cutover run, step 2 sets `CUTOVER` and step 4 is skipped, because the boot preload is already loading the new definition. Step 4 is also skipped, with a warning, if a hold opened after step 1. The Generation window warms Flash-Next itself when it releases. A hold opened between step 1 and a gate restart is still dropped until the next heartbeat, the same gap as any gate restart.

This relaxes the "only `compose.yaml` is distributed" precedent of ADR 0016/0018. The gate and the llama-swap config are real files with their own tests and schema, which inlining them into `compose.yaml` would lose.

## Alternatives considered

- **The hold inside llama-swap** (config edit, or a hook). llama-swap has no lease primitive. Toggling config under `-watch-config` from Temporal gives no automatic lapse if the workflow dies, and races the GitOps workflow.
- **`sendLoadingState: true`.** This streams loading messages into the reasoning field. Agentic clients (Hermes, Dev Loop, Claude Code) would treat them as model output, so they block instead.
- **A llama.cpp-bundled llama-swap image.** It carries an unused inference server and no docker CLI.

## Consequences

- Clients are unchanged outside a Generation window. Inside one, they see `503` + `Retry-After` and must retry or fail over.
- A gate restart during a window drops the hold until the next heartbeat. Keep the heartbeat interval well under the 30-minute TTL; the gap is heartbeat-bounded.
- The Docker socket is now mounted in two containers (llama-swap, mem-watchdog). The risk is the same root-equivalent class ADR 0018 accepted, and it is still homelab-only.
- The watchdog's recovery is no longer "restart itself". It is "the next request relaunches". If no client is calling, Flash-Next stays down after a kill until something asks for it.
- `docker compose up -d --remove-orphans` must not treat the inactive-profile `sglang-server` as an orphan. Verified on the Spark's compose (v5.0.2) before the cutover: a throwaway project kept its inactive-profile container running.
- Verified locally before merge:
  - Gate unit and HTTP tests (`tests/test_gate.py`).
  - The real v260 binary: `-validate` on `llama-swap/config.yaml`.
  - An end-to-end run of the real llama-swap and gate, with a fake SGLang standing in for the managed service. It covered boot preload, relaunch after `kill -9`, the metrics path proxying when loaded and returning `409` without a start when not, and the hold giving `503` + `Retry-After` then `200` after release.
  - A mem-watchdog dry run against a mocked `/proc/meminfo`.
  - None of it touched docker or the Spark.
- **Cutover verification**, run on the Spark on 2026-09-28 (no Generation window yet):
  1. **Pass.** `/v1/models` via `dgx.blosshomelab.com` lists `qwen3.8-flash-next`.
  2. **Pass.** Chat works end to end (2.3s through the gate on an uncontended request). Flash-Next was saturated at its 8-request ceiling right after load; see dgx-llm-server#45.
  3. **Pass.** Prometheus has both targets `up` (`/upstream/qwen3.8-flash-next/metrics`, 88 `sglang_*` series; llama-swap `/metrics`). While the model was down from 00:34 to 00:39, the 30s scrapes did not start it.
  4. **Pass.** A hold gives `503` + `Retry-After: 1800` externally while `/v1/models` stays `200`; the admin port can't be reached from the LAN; chat returns `200` once the hold is released.
  5. **Pass.** `docker kill` by label: llama-swap logged "upstream process exited unexpectedly", and the next client request recreated `sglang-server`. One retry landed in the ~1s before llama-swap noticed the exit and got a `502`.
  6. **Partial.** After a reboot, the gate, llama-swap and mem-watchdog came back and llama-swap logged `preloading model`. But SGLang crashed ~10 min into that load, in its FlashInfer autotune warm-up (`CUDA error: operation not permitted`; no Xid, the same autotune cache as every good load). A retry with an identical config came up healthy, so the crash is intermittent in the dev image. The preload hook doesn't retry, and `sglang-server` is `restart: "no"`, so the LLM stayed down until something asked for it. This is a regression from `restart: unless-stopped`.
- **Boot-preload retry (`flash-next-warm`), added for item 6.** A `docker:27-cli` sidecar (`unless-stopped`) runs on every start, boot included. It waits for llama-swap, then warms Flash-Next through `/upstream/qwen3.8-flash-next/health`, with up to 3 attempts 30s apart, and then idles. It never warms during a Window hold, and it never re-warms later, so it can't fight the generation group for the GPU. Tested in the real image against a fake llama-swap: it retries after failures, gives up after 3, and skips during a hold. Root cause of the SGLang crash: open, upstream.
- **Rollback:** revert the PR and let GitOps restore the always-on `sglang-server`. If `:8000` conflicts while orphans are removed, run `docker compose down && docker compose up -d` by hand.
