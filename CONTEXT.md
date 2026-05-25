# CONTEXT

## Glossary

### DGX Spark
The NVIDIA personal AI supercomputer (GB10 Grace Blackwell) running this project. 128GB unified memory shared between ARM CPU and Blackwell GPU. Runs headless in a homelab.

### NVFP4 Checkpoint
The authoritative model format for this project. HuggingFace repository snapshots quantized to NVFP4 (4-bit floating point), a format native to the Blackwell Tensor Cores on the DGX Spark. Replaces GGUF Quant as of the vLLM migration.

### Model
A single NVFP4 HuggingFace repository representing one LLM served by a dedicated vLLM instance. Both models run simultaneously and remain resident in GPU memory at all times. The two models are:
- `qwen3.6-35b-a3b`: RedHatAI/Qwen3.6-35B-A3B-NVFP4 (~17 GB)
- `qwen3.6-27b`: unsloth/Qwen3.6-27B-NVFP4 (~13 GB)

### vLLM Instance
A single vLLM process serving exactly one model via an OpenAI-compatible HTTP API on a private port. One instance per model. Clients do not talk to vLLM instances directly — all traffic goes through the LiteLLM Proxy.

### LiteLLM Proxy
The single client-facing OpenAI-compatible endpoint (port 8080). Routes incoming requests to the correct vLLM Instance based on the `model` field. Replaces llama.cpp's built-in router mode. Runs as a Docker Compose service alongside the vLLM instances.

### Models Directory
`/home/zbloss/models` on the DGX Spark host, mounted as `/models` inside containers. Contains one subdirectory per NVFP4 model, named after the HuggingFace repo with `/` replaced by `--` (e.g. `RedHatAI--Qwen3.6-35B-A3B-NVFP4/`, `unsloth--Qwen3.6-27B-NVFP4/`). vLLM is pointed at the full directory path.

### Canonical Model Name
The stable, quant-agnostic name clients use in the `model` field of API requests (e.g. `qwen3.6-35b-a3b`, `qwen3.6-27b`). Defined in `models/models.json` and mirrored in the LiteLLM Proxy config. Decoupled from the underlying HuggingFace repo so checkpoint swaps require no client changes.

### GitOps Workflow
The mechanism by which changes to `models/models.json` in the main branch automatically apply to the DGX Spark: a self-hosted GitHub Actions runner on the DGX Spark triggers on push, diffs the model list, downloads new NVFP4 repos via `snapshot_download()`, removes obsolete ones, and restarts the Docker Compose stack.

### Client
Any device on the local network that sends OpenAI-API-compatible requests to the DGX Spark — including Kubernetes pods, Claude Code, and pi.dev.
