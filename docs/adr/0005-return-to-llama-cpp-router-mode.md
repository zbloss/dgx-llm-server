# ADR 0005: Return to llama.cpp — router mode, two models

**Status:** Accepted
**Date:** 2026-08-14
**Supersedes:** ADR 0004
**Un-supersedes:** ADR 0003 (its constraint applies again — see Consequences)

## Context

ADR 0004 moved from llama.cpp (ADR 0002) to vLLM to run `nvidia/Qwen3.6-35B-A3B-NVFP4`, trading away the model-swap flexibility ADR 0002 had specifically adopted llama.cpp for. Qwen3.8-27B released today (2026-08-14) with day-one GGUF support, and the operator wants the ability to swap between it and the existing 35B-A3B model — and other experimental models later — without editing `compose.yaml` each time.

Two things have changed since ADR 0002/0004 were written that make llama.cpp a better fit now than either previous llama.cpp attempt:

1. **NVFP4 tensor-core kernels landed in llama.cpp for Blackwell/GB10 specifically.** [PR #22196](https://github.com/ggml-org/llama.cpp/pull/22196) (merged 2026-04-28) added a Blackwell MMQ kernel benchmarked directly on DGX Spark hardware (`Device 0: NVIDIA GB10, compute capability 12.1`), closing most of the "llama.cpp's CUDA backend is less optimized for the GB10" gap ADR 0002 accepted as a tradeoff. In practice this doesn't change *this* ADR's config, because neither `unsloth/Qwen3.6-35B-A3B-GGUF` nor `unsloth/Qwen3.8-27B-GGUF` ship an NVFP4-quantized `.gguf` file — both only offer K-quants, IQ-quants, and `MXFP4_MOE`/`Q8_0`/`BF16`. We're on `UD-Q4_K_XL` here, same quant family ADR 0002 used, for lack of a published NVFP4 GGUF for either model.
2. **llama.cpp's server gained a real router mode** (`--models-preset`, `--models-max`, per-model INI sections, LRU eviction), replacing the informal `--models-max 1` single-profile swap ADR 0002 described. This is what actually delivers "swap between models via the `model` field, no orchestration code" — ADR 0002 got most of the way there with one model; router mode is built for N.

Both target models are the same architecture family (Gated DeltaNet + Gated Attention hybrid) as ADR 0002's Qwen3.6-27B, and both ship `mmproj-*.gguf` vision projectors and "MTP: trained with multi-steps" per their model cards — the same MTP speculative decoding and vision-via-mmproj combination ADR 0002 relied on carries forward to both.

## Decision

Replace the single vLLM container with `ghcr.io/ggml-org/llama.cpp:server-cuda` in router mode, driven by `models/config.ini`:

- **`qwen3.6-35b-a3b`** (`unsloth/Qwen3.6-35B-A3B-GGUF`, UD-Q4_K_XL) — the model currently in production use. `load-on-startup = true` so it's warm immediately after a restart.
- **`qwen3.8-27b`** (`unsloth/Qwen3.8-27B-GGUF`, UD-Q4_K_XL) — cold-loads on first request.
- `--models-max 1`: only one model resident at a time. This is the same constraint ADR 0002 ran under; router mode adds clean swapping on top of it rather than removing it. Bumping to 2 is possible later but untested here — see Alternatives.
- Both profiles: `ctx-size = 524288`, `parallel = 2` (262144 tokens/slot), `spec-type = draft-mtp`, `ubatch-size = 1024`, `cache-type-k/v = q8_0`, `cache-reuse = 256`.

### Mapping vLLM's tuned settings onto llama.cpp

| vLLM (ADR 0004, latest tuning) | llama.cpp equivalent | Note |
|---|---|---|
| `--max-model-len 262144` | `ctx-size = 524288`, `parallel = 2` (262144/slot) | llama.cpp's `ctx-size` is a *static* total budget split evenly across slots, not a per-request dynamic pool like vLLM's PagedAttention. 524288 is the exact total that ADR 0002's own history (`c6dccd6`, "fix: OOMs") proved fits a single MTP+mmproj model at `parallel=4` (131072/slot) — this ADR spends the same proven budget on 2 slots instead of 4 to hit 262144/slot, rather than testing an untried larger number. |
| `--max-num-seqs 8` | `parallel = 2` | Not a direct translation — see above. 8 concurrent full-length vLLM requests and 2 llama.cpp slots are not equivalent capacity; picked to match vLLM's per-request context instead. |
| `--kv-cache-dtype fp8` | `cache-type-k/v = q8_0` | llama.cpp has no literal fp8 KV cache type (options: f32/f16/bf16/q8_0/q4_0/q4_1/iq4_nl/q5_0/q5_1). q8_0 is the closest byte-width analog, **not validated here** - this is new territory, ADR 0002's config never quantized the KV cache. |
| `--enable-prefix-caching` | `cache-reuse = 256` | Not equivalent - llama.cpp's cache-reuse does KV-shift-based reuse of matching prefixes, more limited than vLLM's radix-tree prefix cache. Also unvalidated; ADR 0002's config never set this either (default is 0/off). |
| `--enable-chunked-prefill`, `--async-scheduling` | `--cont-batching` (default: enabled) | Conceptually the same "don't block decode on prefill" idea; llama.cpp's continuous batching is on by default, nothing to configure. |
| `--attention-backend flashinfer` | `--flash-attn on` | Same intent, different kernel; carried over unchanged from ADR 0002's config. |
| `--reasoning-parser qwen3`, `--tool-call-parser qwen3_xml`, `--enable-auto-tool-choice` | `--jinja` | llama.cpp derives reasoning/tool-call parsing from the GGUF's embedded Jinja chat template rather than dedicated parser flags; `--jinja` (required for tool calling) is the whole story here. |
| `--gpu-memory-utilization 0.75` | *(no equivalent)* | llama.cpp has no utilization-fraction knob; VRAM usage is the sum of `n-gpu-layers` (full offload, both models) plus whatever `ctx-size` × KV dtype reserves. Sizing is done by picking `ctx-size`/`parallel` against a known-good ceiling, not a percentage. |
| *(no vLLM equivalent)* | `--models-max 1`, per-model `load-on-startup` | Router-mode-only concepts; the entire reason for this ADR. |

`ubatch-size = 1024` and `threads = 8` are carried over unchanged from ADR 0002's post-OOM-fix config rather than re-derived from vLLM's `--max-num-batched-tokens 16384` — the two numbers describe different things (llama.cpp's ubatch is a physical per-step compute chunk, vLLM's is a scheduler-level token budget across many sequences) and inflating ubatch-size was never tested against this OOM history.

### Healthcheck

`GET /health` — confirmed exempt from router-mode's task/reload accounting. The old single-model healthcheck POSTed a `/v1/chat/completions` with a hardcoded model name; with two profiles and `--models-max 1`, doing that today would force-evict whatever model the user actually has active every 60 seconds, reproducing the exact swap-cycling failure ADR 0003 documented for Prometheus. `/health` avoids it entirely.

## Alternatives considered

**`--models-max 2` (both models resident simultaneously):** Would eliminate swap latency between the two known models entirely. Rejected for now — ADR 0002's own history shows a *single* MTP+mmproj model at the proven-safe sizing already used the full OOM-tested budget; there's no empirical data on whether two such models fit together on GB10's 128GB unified memory at these context sizes. Worth revisiting once VRAM headroom is measured in practice.

**Reuse NVFP4 quantization instead of GGUF K-quants:** Would keep bit-for-bit parity with the vLLM checkpoints already in use. Rejected because neither target HF repo publishes an NVFP4 `.gguf` — producing one requires the kind of private conversion fork described in third-party reports, not something to build into a GitOps pipeline sight unseen. UD-Q4_K_XL is what ADR 0002 already validated on this hardware.

**Keep vLLM, wait for multi-model support there instead:** vLLM does support serving multiple models, but not with llama.cpp's transparent unload/load-by-request-name; it would need the LiteLLM/routing layer ADR 0004 explicitly removed. Rejected — router mode gets the requested behavior with less moving infrastructure.

## Consequences

- Port reverts from 8000 back to 8080. `k8s/external-service.yaml` and `k8s/ingress-route.yaml` updated accordingly; clients don't need to change anything since they go through `https://dgx.blosshomelab.com` either way.
- `.env` / `HF_TOKEN` is no longer read by the container at all (llama-server serves local files; only the GitHub Actions sync step needs the HF token, as a repo secret). `.env.example` updated to reflect this — the file is currently a placeholder with nothing the stack actually consumes.
- **ADR 0003's constraint is back in force**: never scrape `/metrics` with a `?model=` query parameter. `k8s/service-monitor.yaml` was never changed when ADR 0004 (correctly) called that constraint moot for vLLM, so no action needed there — but it needs to stay that way going forward, unlike ADR 0004's world where it didn't matter.
- Swapping the "default warm" model, or adding a third/experimental model, is now a `models/config.ini` edit + push — no `compose.yaml` change needed unless GPU-level flags change.
- KV cache quantization (`q8_0`) and cache-reuse are new territory for this stack; unlike every other value in this config, they weren't proven under ADR 0002's OOM-fix cycle. Watch for OOMs or quality regressions and be ready to drop `cache-type-k/v` back to `f16` if either shows up.
- `scripts/sync_models.py` and `.github/workflows/sync-models.yml` reuse the exact GGUF-repo download/cleanup pattern ADR 0002 already built (`allow_patterns` filtering to one quant + mmproj, stray-`.gguf` cleanup, `models/config.ini` copied to the deploy host alongside `compose.yaml`) — nothing new invented here, just restored.
