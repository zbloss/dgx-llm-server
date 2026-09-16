# CONTEXT

## Glossary

### DGX Spark
The NVIDIA personal AI supercomputer (GB10 Grace Blackwell) running this project. 128GB unified memory shared between ARM CPU and Blackwell GPU. Runs headless in a homelab.

### sglang-server
A single `sglang-server` container (`lmsysorg/sglang:dev-qwen38-next-local`, a non-default dev image built from SGLang's unmerged `qwen4-main-squashed` integration branch) serving an OpenAI-compatible HTTP API on port 8000 (explicit override - SGLang defaults to 30000/127.0.0.1), running `RadixArk/Qwen3.8-Flash-Next-NVFP4` under the served model name `qwen3.8-flash-next` (`--served-model-name`) - the only model this stack serves (ADR 0016, replacing the prior `vllm-server`/`qwen3.8-27b` setup). No model-swap-by-name: exactly one resident model. No `--api-key` is set, so the endpoint has no built-in auth; access is scoped by network/Traefik routing only. `compose.yaml` overrides the container `entrypoint` to force-delete and repopulate the ~47.7GB PLE (n-gram embedding) table on every start, offloaded to local NVMe (`--ple-offload-backend file`) since it's required just to fit the model in 128GB unified memory.

### Qwen3.8-27B NVFP4 (removed, ADR 0016)
`unsloth/Qwen3.8-27B-NVFP4` - the prior served model (ADR 0010-0015), a native NVFP4 safetensors checkpoint. Removed from `models/models.json` and no longer served as of ADR 0016's replacement with `qwen3.8-flash-next`. Kept here for historical reference only.

### DFlash2 draft model (removed, ADR 0016)
`z-lab/Qwen3.8-27B-DFlash2` - a ~1.9B-parameter block-diffusion speculative-decoding draft model for the prior `qwen3.8-27b` checkpoint. Adopted in ADR 0013, reverted (unused but kept downloaded) in ADR 0015, removed entirely from `models/models.json` in ADR 0016 since the target checkpoint it was drafting for is no longer served.

### Models Directory
`/home/zbloss/models` on the DGX Spark host, mounted read-only as `/models` inside the container. Contains one directory per HF repo (`RadixArk--Qwen3.8-Flash-Next-NVFP4/`, per ADR 0016), downloaded whole - no `allow_patterns` filter, since it's a single-file safetensors repo rather than a multi-quant GGUF repo.

### GitOps Workflow
Changes to `models/models.json` or `compose.yaml` on `main` trigger the self-hosted GitHub Actions runner (`.github/workflows/sync-models.yml`): clears the stale PLE table cache, runs `scripts/sync_models.py` to download new HuggingFace repos via `snapshot_download()` and remove obsolete ones, then copies `compose.yaml` into place and runs `docker compose up -d --remove-orphans` (per ADR 0016). No `.env` file is written or needed - `sglang-server` runs with `HF_HUB_OFFLINE=1` against pre-downloaded local files and never talks to HuggingFace itself; only the GitHub Actions step needs `HF_TOKEN`, as a repo secret.

### Client
Any device on the local network that sends OpenAI-API-compatible requests to the DGX Spark - including Kubernetes pods, Claude Code, and pi.dev. Connects via `OPENAI_BASE_URL=https://dgx.blosshomelab.com/v1`, routed through Traefik to the DGX Spark's fixed IP on port 8000. The `model` field should be `qwen3.8-flash-next` (the `--served-model-name`; the underlying checkpoint is `RadixArk/Qwen3.8-Flash-Next-NVFP4`, per ADR 0016); there is only one model to select.

### Agent Loop
The deterministic five-phase sequence agents run per project:
1. Planning
2. Implementation
3. Repeat Implementation until plan is complete
4. Merge
5. QA Validation

### QA Agent
The agent role responsible for Phase 5 of the Agent Loop. Responsible for: iterating over API endpoints, launching a Chrome browser via MCP tools, taking and analyzing screenshots, verifying frontend correctness, and tracing E2E data flows through backend systems. The prior `unsloth/Qwen3.8-27B-NVFP4` was a text-only model (no `mmproj` vision projector); whether `qwen3.8-flash-next` (`RadixArk/Qwen3.8-Flash-Next-NVFP4`, ADR 0016) has native vision support was not checked as part of that ADR - unverified, revisit if the QA agent starts depending on native vision.

---

## Recent architecture history

The model stack has changed several times; see `docs/adr/` for full rationale. Current state (ADR 0016, accepted 2026-09-16, unverified pending deploy) is **SGLang serving a single model, `qwen3.8-flash-next` (`RadixArk/Qwen3.8-Flash-Next-NVFP4`), with its PLE table offloaded to local NVMe**.

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
11. **ADR 0015** (accepted, amends 0013/0014, reverts to 0011): further testing (Dockerized guidellm, see `guidellm/`) found DFlash2 gives 0% prefix-cache hit rate on this model's hybrid GDN architecture - confirmed on the then-current image *and* on the oldest possible DFlash2-capable build, ruling out "wait for a newer image." For agentic-coding traffic (large, mostly-repeated context every turn), this made DFlash2 slower than no speculative decoding past about 4-5 turns. Reverted to the exact ADR-0011 config (image, MTP `num_speculative_tokens:5`, no autotune flags) - MTP only gets partial caching (~36.5%, a known, separate upstream limitation) but beats DFlash2's 0%. Revisit when `vllm-project/vllm#52244` or an equivalent fix merges upstream.
12. **ADR 0016** (accepted, amends 0010, revisits/re-scopes 0013's SGLang rejection): replaced `qwen3.8-27b`/vLLM entirely with `qwen3.8-flash-next` (`RadixArk/Qwen3.8-Flash-Next-NVFP4`, a 176B/6B-active hybrid MoE) served via SGLang, on a non-default dev image (`lmsysorg/sglang:dev-qwen38-next-local`, built from an unmerged integration branch) with its ~47.7GB PLE table offloaded to local NVMe - required just to fit in 128GB unified memory, not a performance choice. ADR 0013's SGLang-migration rejection was re-verified as still accurate but scoped narrowly to this one model rather than a whole-stack migration. `compose.yaml` overrides the container entrypoint to force-delete and rebuild the PLE table fresh on every start (`rm -rf /ple-cache/* && exec sglang serve ...`), avoiding a ~55min slow-rebuild path in favor of a ~10min one; healthcheck `start_period` raised to 900s accordingly. Ships without speculative decoding for this first pass (SGLang's MTP-equivalent, `--speculative-algorithm NEXTN`, has unverified required sub-flags) and with `auto`-resolved reasoning/tool-call parsers (no explicit `qwen4`-family parser exists in SGLang yet).

If you're touching `compose.yaml` or the model stack, treat the ADRs as historical rationale rather than a current-state reference - cross-check against `compose.yaml`, `models/models.json`, and `k8s/*.yaml` directly.
