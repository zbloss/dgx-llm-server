# dgx-llm-server

Serves a quantized LLM from a DGX Spark over the local network via an OpenAI-compatible API. Models are managed through git - push a config change to swap the model without touching the DGX Spark.

**Endpoint:** `https://dgx.blosshomelab.com`

---

## How it works

- **A single vLLM server** (`vllm/vllm-openai:nightly`) serves `unsloth/Qwen3.8-27B-NVFP4` on port 8000. There is no model-swap-by-name - one model, always resident.
- **`models/models.json`** is the GitOps manifest: push a change here and the self-hosted GitHub Actions runner on the DGX Spark downloads the new HuggingFace repo, removes the obsolete one, and restarts the stack.
- **`compose.yaml`** carries the vLLM launch flags (context length, batching, speculative decoding, tool/reasoning parsers) - edit it directly to retune the model.
- **Traefik** in the homelab K8s cluster terminates TLS and routes `dgx.blosshomelab.com` to the DGX Spark's fixed IP on port 8000. The manifests that actually do this live in the `home-server` GitOps repo (`kubernetes/apps/ml/dgx-vllm/`, Flux-managed) - `k8s/` in *this* repo is an illustrative example only, kept for reference, not applied anywhere.
- The endpoint has no API key auth - access is scoped by network/Traefik routing, not by a bearer token.

---

## Prerequisites (DGX Spark)

- Docker with [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)
- `huggingface-cli` available on the runner (`pip install huggingface_hub[cli]`)
- GitHub Actions self-hosted runner registered with label `dgx-spark`
- CUDA 12.8+ (required for Blackwell/SM120)

---

## First-time setup

**1. Register the self-hosted runner on the DGX Spark:**

Go to your GitHub repo → Settings → Actions → Runners → New self-hosted runner.
Follow the instructions, and when prompted for labels add `dgx-spark`.

**2. Start the server manually for the first run:**
```bash
docker compose up -d
```
Watch progress as the model loads:
```bash
docker compose logs -f vllm-server
```

**3. Enable the systemd service so the stack starts on boot:**

Create `/etc/systemd/system/dgx-llm-server.service`:
```ini
[Unit]
Description=DGX LLM Server (vLLM docker compose stack)
Requires=docker.service
After=docker.service network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/home/zbloss/Projects/dgx-llm-server
ExecStart=/usr/bin/docker compose up -d --remove-orphans
ExecStop=/usr/bin/docker compose down
TimeoutStartSec=300

[Install]
WantedBy=multi-user.target
```
Then enable it:
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now dgx-llm-server.service
```

**4. Apply the K8s manifests:**

The manifests that actually route `dgx.blosshomelab.com` live in the `home-server` GitOps repo (`kubernetes/apps/ml/dgx-vllm/`), applied automatically by Flux. `k8s/` in *this* repo is an illustrative example only - useful as a reference for what the real ones look like, but not something you `kubectl apply` here. To change routing, DGX Spark IP, or Prometheus scraping, edit the files in `home-server` instead.

**5. Add `HF_TOKEN` as a GitHub Actions secret** (repo → Settings → Secrets and variables → Actions → New repository secret). Required to download the model from HuggingFace during the GitOps sync - the `vllm-server` container itself never talks to HuggingFace (`HF_HUB_OFFLINE=1`).

**6. Trigger the first model download:**

Push any change to `models/models.json` or `compose.yaml`, or run the workflow manually from the Actions tab.

---

## Swapping the model

1. Update the entry in `models/models.json` (`name`, `hf_repo`, and an optional `allow_patterns` filter if the new repo publishes multiple quant variants and you only want one).
2. Update `compose.yaml`'s `--model` / `--served-model-name` to match the new local path and repo id.
3. Push to `main`.

The GitHub Actions workflow runs on the DGX Spark, downloads the new model, removes the obsolete directory, and restarts the stack.

---

## Model

| Served model name | Model | Format | HF repo | GPU |
|---|---|---|---|---|
| `qwen3.8-27b` | Qwen3.8-27B (dense) | NVFP4 safetensors | `unsloth/Qwen3.8-27B-NVFP4` | full offload, tensor-parallel-size 1 |

262,144-token context, DFlash2 speculative decoding (`z-lab/Qwen3.8-27B-DFlash2` draft model, downloaded alongside the main checkpoint), chunked prefill, prefix caching, `gpu-memory-utilization 0.85`. See `docs/adr/0010-return-to-vllm-qwen38-27b-nvfp4.md`, `docs/adr/0011-vllm-perf-tuning-gpu-memory-and-batching.md`, `docs/adr/0012-flashinfer-autotune-and-mtp-crash-risk.md`, and `docs/adr/0013-dflash2-speculative-decoding.md` for the reasoning behind these settings.

---

## Client configuration

All clients use `https://dgx.blosshomelab.com/v1` as the base URL and `qwen3.8-27b` in the `model` field. No API key is required.

**Claude Code / shell environment:**
```bash
export OPENAI_BASE_URL=https://dgx.blosshomelab.com/v1
export OPENAI_API_KEY=unused
```

**Pi.dev (`~/.config/pi/models.json`):**
```json
{
  "providers": [{
    "name": "dgx-spark",
    "type": "openai",
    "baseUrl": "https://dgx.blosshomelab.com/v1",
    "apiKey": "unused",
    "models": ["qwen3.8-27b"]
  }]
}
```

**Python / K8s workloads:**
```python
from openai import OpenAI

client = OpenAI(
    base_url="https://dgx.blosshomelab.com/v1",
    api_key="unused",
)
response = client.chat.completions.create(
    model="qwen3.8-27b",
    messages=[{"role": "user", "content": "Hello"}],
)
```

**Kubernetes pod environment variables:**
```yaml
env:
  - name: OPENAI_BASE_URL
    value: "https://dgx.blosshomelab.com/v1"
  - name: OPENAI_API_KEY
    value: "unused"
```

---

## Verify GPU offload

After startup, confirm the model loaded successfully:
```bash
docker compose logs vllm-server | grep -i "error\|loaded weights"
curl -s http://localhost:8000/v1/models | jq .
```

---

## File reference

| File | Purpose |
|---|---|
| `compose.yaml` | Docker Compose: single `vllm-server` container with all vLLM launch flags |
| `models/models.json` | GitOps manifest: HuggingFace repo (and optional quant filter) for the model |
| `k8s/*.yaml` | Illustrative examples only - not applied anywhere. The real manifests (Service, IngressRoute/HTTPRoute, ServiceMonitor) live in `home-server`'s `kubernetes/apps/ml/dgx-vllm/`, Flux-managed. |
| `.github/workflows/sync-models.yml` | GitOps workflow (runs on DGX Spark self-hosted runner) |
| `scripts/sync_models.py` | Downloads the model repo (filtered by `allow_patterns` if set), removes obsolete ones |
| `docs/adr/` | Architecture decision records for the model stack's history |
