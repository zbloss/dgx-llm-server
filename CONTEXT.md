# CONTEXT

## Glossary

### DGX Spark
The NVIDIA personal AI supercomputer (GB10 Grace Blackwell) running this project. 128GB unified memory shared between ARM CPU and Blackwell GPU. Runs headless in a homelab.

### Model
`qwen3.6-35b-a3b` — Qwen3.6-35B-A3B MoE in NVFP4 quantization. Served by vLLM nightly with FlashInfer attention, Marlin MoE backend, and MTP speculative decoding. Source: `nvidia/Qwen3.6-35B-A3B-NVFP4`. 128K context, prefix caching, chunked prefill.

### NVFP4 Checkpoint
NVIDIA's FP4 quantization format for MoE models. Loaded by vLLM with `--load-format fastsafetensors`. Repo: `nvidia/Qwen3.6-35B-A3B-NVFP4`.

### vLLM Server
A single `llm-server` container serving the model via an OpenAI-compatible HTTP API on port 8000. Uses the vLLM nightly image with FlashInfer, Marlin MoE, prefix caching, chunked prefill, async scheduling, and MTP speculative decoding (3 tokens, Triton backend).

### Models Directory
`/home/zbloss/models` on the DGX Spark host, mounted as `/models` inside the container. Contains `nvidia--Qwen3.6-35B-A3B-NVFP4/` downloaded by the sync script.

### Canonical Model Name
`qwen3.6-35b-a3b` — the name clients use in the `model` field of API requests. Resolved by vLLM from the `serve` command.

### Agent Loop
The deterministic five-phase sequence agents run per project:
1. Planning
2. Implementation
3. Repeat Implementation until plan is complete
4. Merge
5. QA Validation

All phases use `qwen3.6-35b-a3b` via vLLM.

### QA Agent
The agent role responsible for Phase 5 of the Agent Loop. Uses `qwen3.6-35b-a3b` via vLLM. Responsible for: iterating over API endpoints, launching a Chrome browser via MCP tools, taking and analyzing screenshots, verifying frontend correctness, and tracing E2E data flows through backend systems.

### GitOps Workflow
Changes to `models/models.json` or `compose.yaml` on main trigger the self-hosted GitHub Actions runner: downloads model weights via `snapshot_download()`, removes obsolete repos, applies compose.yaml, and restarts the vLLM container.

### Client
Any device on the local network that sends OpenAI-API-compatible requests to the DGX Spark — including Kubernetes pods, Claude Code, and pi.dev. Connects via `OPENAI_BASE_URL=http://<dgx-ip>:8000`.
