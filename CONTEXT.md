# CONTEXT

## Glossary

### DGX Spark
The NVIDIA personal AI supercomputer (GB10 Grace Blackwell) running this project. 128GB unified memory shared between ARM CPU and Blackwell GPU. Runs headless in a homelab.

### llama-server (Router Mode)
A single `llama-server` container (`ghcr.io/ggml-org/llama.cpp:server-cuda`) serving an OpenAI-compatible HTTP API on port 8080, launched with `--models-preset /config.ini --models-max 1`. Router mode holds a roster of named model profiles and transparently loads/unloads whichever one a request's `model` field names — only one profile is resident in GPU memory at a time. No `--api-key` is set, so the endpoint has no built-in auth; access is scoped by network/Traefik routing only.

### Model Profile
A `[name]` section in `models/config.ini` defining one servable model: GGUF file path, `mmproj` vision projector path, `ctx-size`, `parallel` (slot count), MTP speculative decoding flags, KV cache quantization, etc. Global defaults shared by every profile live in the `[*]` section. Currently defined profiles:
- `qwen3.6-35b-a3b` — Qwen3.6-35B-A3B MoE (256 experts, 8 routed + 1 shared active, 3B active params), `unsloth/Qwen3.6-35B-A3B-GGUF` at UD-Q4_K_XL. Loads at container startup (`load-on-startup = true`) — this is the model in active production use.
- `qwen3.8-27b` — Qwen3.8-27B dense hybrid (Gated DeltaNet + Gated Attention), `unsloth/Qwen3.8-27B-GGUF` at UD-Q4_K_XL. Released 2026-08-14; cold-loads on first request.

Both profiles: 262,144-token context per slot (524,288 total ÷ 2 parallel slots), MTP speculative decoding (trained into both models natively — not a separate draft-model download), native vision via `mmproj`, q8_0 KV cache. See `docs/adr/0005-return-to-llama-cpp-router-mode.md` for how these numbers were derived from a previously-proven-safe OOM ceiling rather than guessed fresh.

### GGUF Checkpoint
llama.cpp's native quantized checkpoint format. Both current models use Unsloth's `UD-Q4_K_XL` dynamic quant. Neither `unsloth/Qwen3.6-35B-A3B-GGUF` nor `unsloth/Qwen3.8-27B-GGUF` publish an NVFP4-quantized `.gguf` as of 2026-08-14, even though llama.cpp gained native Blackwell-accelerated NVFP4 tensor support in April 2026 (see ADR 0005) — so this stack is not using NVFP4 despite the hardware and software both supporting it, purely because no published GGUF exists yet for these models in that format.

### Models Directory
`/home/zbloss/models` on the DGX Spark host, mounted read-only as `/models` inside the container. Contains one directory per HF repo (`unsloth--Qwen3.6-35B-A3B-GGUF/`, `unsloth--Qwen3.8-27B-GGUF/`), each holding just the one quant file + `mmproj-BF16.gguf` selected via `allow_patterns` in `models/models.json` — not the full multi-quant repo.

### GitOps Workflow
Changes to `models/models.json`, `models/config.ini`, or `compose.yaml` on `main` trigger the self-hosted GitHub Actions runner (`.github/workflows/sync-models.yml`): runs `scripts/sync_models.py` to download new HuggingFace repos via `snapshot_download()` (filtered by each model's `allow_patterns`) and remove obsolete ones, then copies `compose.yaml` and `models/config.ini` into place and runs `docker compose up -d --remove-orphans`. No `.env` file is written or needed — `llama-server` serves pre-downloaded local files and never talks to HuggingFace itself; only the GitHub Actions step needs `HF_TOKEN`, as a repo secret.

### Client
Any device on the local network that sends OpenAI-API-compatible requests to the DGX Spark — including Kubernetes pods, Claude Code, and pi.dev. Connects via `OPENAI_BASE_URL=https://dgx.blosshomelab.com/v1`, routed through Traefik to the DGX Spark's fixed IP on port 8080. The `model` field selects a `models/config.ini` profile by name (`qwen3.6-35b-a3b` or `qwen3.8-27b`); the router loads it on first request if it isn't already active.

### Agent Loop
The deterministic five-phase sequence agents run per project:
1. Planning
2. Implementation
3. Repeat Implementation until plan is complete
4. Merge
5. QA Validation

### QA Agent
The agent role responsible for Phase 5 of the Agent Loop. Responsible for: iterating over API endpoints, launching a Chrome browser via MCP tools, taking and analyzing screenshots, verifying frontend correctness, and tracing E2E data flows through backend systems. Both current model profiles ship an `mmproj` vision projector, so either can serve this phase — a prior version of this document flagged vision support as an open question for the vLLM-era model; that's resolved now that both GGUF repos confirm native vision support.

---

## Recent architecture history

The model stack has changed several times; see `docs/adr/` for full rationale. Current state (ADR 0005, accepted 2026-08-14) is **llama.cpp in router mode, serving two GGUF model profiles with transparent swap-by-request-name**.

1. **ADR 0001** (superseded): two-model vLLM stack (Nemotron-3-Super text + Qwen3-VL-32B vision) behind LiteLLM. Never worked in practice — combined KV cache requirements didn't fit in 128GB.
2. **ADR 0002** (superseded by 0004, then effectively restored by 0005): dropped vLLM/LiteLLM for a single llama.cpp server running Qwen3.6-27B (GGUF, MTP speculative decoding, vision via `--mmproj`), using the informal `--models-max 1` single-profile swap.
3. **ADR 0003** (superseded by 0004, **back in force as of 0005**): llama.cpp-specific fix — Prometheus must scrape `/metrics` without a `?model=` param, since that param triggers a model load/unload cycle on llama.cpp. `k8s/service-monitor.yaml` was never reverted when this constraint went moot under ADR 0004, so no action was needed to restore it, but it matters again now.
4. **ADR 0004** (superseded by 0005): migrated from llama.cpp back to vLLM with `nvidia/Qwen3.6-35B-A3B-NVFP4` as the sole model. Port changed from 8080 to 8000.
5. **ADR 0005** (accepted): migrated back to llama.cpp, this time using its real router mode (`--models-preset`, `--models-max`, LRU eviction) instead of ADR 0002's informal single-profile swap. Two profiles: the existing `qwen3.6-35b-a3b` (re-encoded as GGUF) and the newly-released `qwen3.8-27b`. Port reverts to 8080. vLLM's tuned settings (`max-model-len`, `max-num-seqs`, fp8 KV cache, prefix caching) were mapped onto llama.cpp equivalents where one exists — several (KV cache dtype, prefix caching) are approximations rather than exact translations, since the two engines' KV cache management (static per-slot reservation vs. PagedAttention) isn't directly comparable. See the ADR for the full mapping table and what's still unvalidated.

If you're touching `compose.yaml` or the model stack, treat the ADRs as historical rationale rather than a current-state reference — cross-check against `compose.yaml`, `models/config.ini`, `models/models.json`, and `k8s/*.yaml` directly.
