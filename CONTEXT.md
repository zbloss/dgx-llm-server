# CONTEXT

## Glossary

### DGX Spark
The NVIDIA personal AI supercomputer (GB10 Grace Blackwell) running this project. 128GB unified memory shared between ARM CPU and Blackwell GPU. Runs headless in a homelab.

### GGUF Checkpoint
The authoritative model format for this project. GGUF files quantized using Unsloth Dynamic 2.0 imatrix calibration (UD-Q4_K_XL), served by the llama.cpp Server. Replaced NVFP4 HuggingFace checkpoints when the project returned to llama.cpp.

### Model
`qwen3.6-27b-mtp` — the single model serving all Agent Loop phases. Qwen3.6-27B dense vision-language model with Multi-Token Prediction speculative decoding (~1.5–2× faster inference) and vision via mmproj. Source: `unsloth/Qwen3.6-27B-MTP-GGUF` (UD-Q4_K_XL). LiveCodeBench v6: 83.9%, SWE-bench Verified: 77.2%.

### Model Profile
The named section in `models/config.ini` that binds a Canonical Model Name to a GGUF Checkpoint path and its llama.cpp startup flags. A single profile (`qwen3.6-27b-mtp`) is active. Any HTTP request — including Prometheus scrapes with a `?model=` query parameter — acts as a model load trigger, so scraping must never reference a model name not currently loaded. See ADR 0003.

### Agent Loop
The deterministic five-phase sequence agents run per project:
1. Planning
2. Implementation
3. Repeat Implementation until plan is complete
4. Merge
5. QA Validation

Phases 1–4 use the MTP Profile (`qwen3.6-27b-mtp`). Phase 5 uses the Vision Profile (`qwen3.6-27b`). The model swap is triggered automatically when the QA Agent makes its first API call — no orchestration code required.

### QA Agent
The agent role responsible for Phase 5 of the Agent Loop. Uses the Vision Profile as its primary model. Responsible for: iterating over API endpoints, launching a Chrome browser via MCP tools, taking and analyzing screenshots, verifying frontend correctness, and tracing E2E data flows through backend systems.

### llama.cpp Server
A single `llama-server` container serving all agent requests via an OpenAI-compatible HTTP API on port 8080. Loads exactly one Model Profile at a time (`--models-max 1`). Auto-detects `n_parallel = 4` for concurrent request handling. Clients request a model by name; the server handles loading and unloading transparently. Any request — including Prometheus metric scrapes with a `?model=` query parameter — acts as a model load trigger. See ADR 0003.

### Models Directory
`/home/zbloss/models` on the DGX Spark host, mounted as `/models` inside the llama.cpp Server container. Contains one subdirectory per downloaded HuggingFace repo, named with `/` replaced by `--` (e.g. `unsloth--Qwen3.6-27B-MTP-GGUF/`). Model Profiles in `config.ini` reference absolute paths within this directory.

### Canonical Model Name
The stable, checkpoint-agnostic name clients use in the `model` field of API requests (e.g. `qwen3.6-27b-mtp`, `qwen3.6-27b`). Matches a Model Profile name in `models/config.ini`. Decoupled from the underlying GGUF filename so checkpoint swaps require no client changes.

### GitOps Workflow
The mechanism by which changes to `models/models.json` in the main branch automatically apply to the DGX Spark: a self-hosted GitHub Actions runner on the DGX Spark triggers on push, diffs the model list, downloads new GGUF repos via `snapshot_download()`, removes obsolete ones, and restarts the llama.cpp Server container.

### Client
Any device on the local network that sends OpenAI-API-compatible requests to the DGX Spark — including Kubernetes pods, Claude Code, and pi.dev.
