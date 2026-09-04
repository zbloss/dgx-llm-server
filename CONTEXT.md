# CONTEXT

## Glossary

### DGX Spark
The NVIDIA personal AI supercomputer (GB10 Grace Blackwell) running this project. 128GB unified memory shared between ARM CPU and Blackwell GPU. Runs headless in a homelab.

### vllm-server
A single `vllm-server` container (`ghcr.io/spark-arena/dgx-vllm-eugr-nightly`, a digest-pinned mirror of `eugr/spark-vllm`'s vLLM nightly builds) serving an OpenAI-compatible HTTP API on port 8000, running `unsloth/Qwen3.8-27B-NVFP4` under the served model name `qwen3.8-27b` (`--served-model-name`) - the only model this stack serves. No model-swap-by-name: unlike the previous llama.cpp router-mode setup, there is exactly one resident model. No `--api-key` is set, so the endpoint has no built-in auth; access is scoped by network/Traefik routing only.

### DFlash2 draft model
`z-lab/Qwen3.8-27B-DFlash2` - a ~1.9B-parameter block-diffusion speculative-decoding draft model for the target `Qwen/Qwen3.8-27B` checkpoint, downloaded to `/models/z-lab--Qwen3.8-27B-DFlash2` via the same GitOps sync as the main model. Used via `--speculative-config '{"method":"dflash",...}'` (ADR 0013), replacing the target checkpoint's own bundled MTP head. Predicts a whole block of draft tokens per forward pass instead of one at a time; decoding stays lossless (verified against the target model, same as MTP).

### Qwen3.8-27B NVFP4
`unsloth/Qwen3.8-27B-NVFP4` - a native NVFP4 safetensors checkpoint (not GGUF). Released as a day-one NVFP4 quant, closing the gap that forced the prior llama.cpp-era stack (ADR 0005) onto GGUF K-quants for lack of a published NVFP4 GGUF. Ships a bundled `model_mtp.safetensors` (unused since ADR 0013 - see below) alongside the main weights.

### Models Directory
`/home/zbloss/models` on the DGX Spark host, mounted read-only as `/models` inside the container. Contains one directory per HF repo (`unsloth--Qwen3.8-27B-NVFP4/`), downloaded whole - no `allow_patterns` filter, since it's a single-file safetensors repo rather than a multi-quant GGUF repo.

### GitOps Workflow
Changes to `models/models.json` or `compose.yaml` on `main` trigger the self-hosted GitHub Actions runner (`.github/workflows/sync-models.yml`): runs `scripts/sync_models.py` to download new HuggingFace repos via `snapshot_download()` and remove obsolete ones, then copies `compose.yaml` into place and runs `docker compose up -d --remove-orphans`. No `.env` file is written or needed - `vllm-server` runs with `HF_HUB_OFFLINE=1` against pre-downloaded local files and never talks to HuggingFace itself; only the GitHub Actions step needs `HF_TOKEN`, as a repo secret.

### Client
Any device on the local network that sends OpenAI-API-compatible requests to the DGX Spark - including Kubernetes pods, Claude Code, and pi.dev. Connects via `OPENAI_BASE_URL=https://dgx.blosshomelab.com/v1`, routed through Traefik to the DGX Spark's fixed IP on port 8000. The `model` field should be `qwen3.8-27b` (the `--served-model-name`; the underlying checkpoint is `unsloth/Qwen3.8-27B-NVFP4`); there is only one model to select.

### Agent Loop
The deterministic five-phase sequence agents run per project:
1. Planning
2. Implementation
3. Repeat Implementation until plan is complete
4. Merge
5. QA Validation

### QA Agent
The agent role responsible for Phase 5 of the Agent Loop. Responsible for: iterating over API endpoints, launching a Chrome browser via MCP tools, taking and analyzing screenshots, verifying frontend correctness, and tracing E2E data flows through backend systems. `unsloth/Qwen3.8-27B-NVFP4` is a text model - unlike the prior llama.cpp-era GGUF checkpoints, this NVFP4 repo does not ship an `mmproj` vision projector, so vision-dependent QA steps are not currently served by this stack. Revisit if the QA agent starts depending on native vision.

---

## Recent architecture history

The model stack has changed several times; see `docs/adr/` for full rationale. Current state (ADR 0010, accepted 2026-08-15) is **vLLM serving a single model, `qwen3.8-27b` (`unsloth/Qwen3.8-27B-NVFP4`)**.

1. **ADR 0001** (superseded): two-model vLLM stack (Nemotron-3-Super text + Qwen3-VL-32B vision) behind LiteLLM. Never worked in practice - combined KV cache requirements didn't fit in 128GB.
2. **ADR 0002** (superseded by 0004, then effectively restored by 0005): dropped vLLM/LiteLLM for a single llama.cpp server running Qwen3.6-27B (GGUF, MTP speculative decoding, vision via `--mmproj`), using the informal `--models-max 1` single-profile swap.
3. **ADR 0003** (superseded by 0004, back in force under 0005, moot again under 0010): llama.cpp-specific fix - Prometheus must scrape `/metrics` without a `?model=` param, since that param triggers a model load/unload cycle on llama.cpp. Not applicable to vLLM.
4. **ADR 0004** (superseded by 0005): migrated from llama.cpp back to vLLM with `nvidia/Qwen3.6-35B-A3B-NVFP4` as the sole model. Port changed from 8080 to 8000.
5. **ADR 0005** (superseded by 0010): migrated back to llama.cpp router mode to serve two swappable GGUF profiles (`qwen3.6-35b-a3b`, `qwen3.8-27b`). Port reverted to 8080.
6. **ADR 0010** (accepted): dropped `qwen3.6-35b-a3b` (low actual usage) and moved `qwen3.8-27b` off GGUF/llama.cpp onto vLLM, now that `unsloth/Qwen3.8-27B-NVFP4` exists as a native NVFP4 checkpoint with bundled MTP tensors. Back to a single vLLM container, port 8000, no model-swap-by-name.
7. **ADR 0011** (accepted, amends 0010): empirical GPU-memory/batching retune - `gpu-memory-utilization` 0.45→0.85, `max-num-seqs` 4→8, `max-num-batched-tokens` 8192→32768, default `reasoning_effort: medium`, persisted `/root/.cache/vllm` across restarts.
8. **ADR 0012** (accepted, amends 0010/0011): added `--enable-flashinfer-autotune` (unverified pending deploy); rejected several community-sourced env vars as redundant with this image's build-time defaults; documented (without acting on) a third-party report of MTP speculative decoding causing a full host reboot under concurrent load.
9. **ADR 0013** (accepted, amends 0010/0012): considered and rejected switching the whole stack to SGLang (unofficial GB10 support, modest measured edge over vLLM); instead bumped the vLLM image ~3 weeks and replaced MTP speculative decoding with `z-lab/Qwen3.8-27B-DFlash2`, a block-diffusion draft model with native (now-merged) vLLM support.
10. **ADR 0014** (accepted, amends 0013): the ADR-0013 deploy crashed the DGX Spark host three times via a FlashInfer autotune OOM/hang. Root cause: `--enable-flashinfer-autotune`'s underlying `KernelConfig.enable_flashinfer_autotune` defaults to `None`, and vLLM's gate only skips autotuning on an explicit `False` - omitting the flag left it effectively on. Fixed with `--kernel-config.enable_flashinfer_autotune=False` (a dotted nested-config override); DFlash2 itself was never at fault, and post-fix throughput (50.1 tok/s) beats both the ADR-0011 baseline and the SGLang-comparison's own vLLM+DFlash2 figure.

If you're touching `compose.yaml` or the model stack, treat the ADRs as historical rationale rather than a current-state reference - cross-check against `compose.yaml`, `models/models.json`, and `k8s/*.yaml` directly.
