# CONTEXT

## Glossary

### DGX Spark
The NVIDIA personal AI supercomputer (GB10 Grace Blackwell) running this project. 128GB unified memory shared between ARM CPU and Blackwell GPU. Runs headless in a homelab.

### GGUF Quant
A quantized model file in GGUF format, as produced by llama.cpp and Unsloth. The authoritative model format for this project. Unsloth Dynamic quants (UD-Q6_K_XL, UD-Q8_0) are used because full BF16/FP16 HuggingFace weights for the target 80B MoE model exceed the 128GB memory ceiling.

### Model
A single GGUF quant file representing one LLM. Models are loaded one at a time. The two initial models are Qwen3-Coder-Next (80B-A3B, UD-Q6_K_XL) and Qwen3.6-35B-A3B (UD-Q8_0).

### Router Mode
The llama.cpp server operating mode where no model is pre-loaded. The server auto-discovers GGUF files in `--models-dir`, loads a model on first request by name, and evicts least-recently-used models when the `--models-max` limit is reached. This is the operational mode for this project.

### Models Directory
`/home/zbloss/models` on the DGX Spark host, mounted as `/models` inside the container. Contains GGUF files for all managed models.

### Model Preset Config
`models/config.ini` in this repo, mounted into the container. Maps canonical model names (e.g. `qwen3-coder`) to their GGUF file paths and per-model parameters (context size, GPU layers). This is the authoritative source for which models are available and under what names. Clients use the canonical name in the `model` field of API requests.

### Canonical Model Name
The stable, quant-agnostic name clients use in the `model` field of API requests (e.g. `qwen3-coder`, `qwen3-35b-a3b`). Defined in `models/config.ini`. Decoupled from the underlying GGUF filename so quant swaps require no client changes.

### GitOps Workflow
The mechanism by which changes to `models/config.ini` in the main branch automatically apply to the DGX Spark: a self-hosted GitHub Actions runner on the DGX Spark triggers on push, diffs the config, downloads new GGUF files, removes old ones, and restarts the Docker Compose stack.

### Client
Any device on the local network that sends OpenAI-API-compatible requests to the DGX Spark — including Kubernetes pods, Claude Code, and pi.dev.
