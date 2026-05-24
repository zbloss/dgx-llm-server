# dgx-llm-server

Serves quantized LLMs from a DGX Spark over the local network via an OpenAI-compatible API. Models are managed through git — push a config change to swap or add a model without touching the DGX Spark.

**Endpoint:** `https://dgx.blosshomelab.com`

---

## How it works

- **Two vLLM instances** run simultaneously, one per model, each loading an NVFP4 checkpoint optimized for the Blackwell GB10 GPU.
- **LiteLLM proxy** sits in front on port 8080, routing requests to the correct vLLM instance based on the `model` field.
- **`models/models.json`** is the GitOps manifest: push a change here and the self-hosted GitHub Actions runner on the DGX Spark downloads new NVFP4 repos, removes obsolete ones, and restarts the stack.
- **Traefik** in the homelab K8s cluster terminates TLS and routes `dgx.blosshomelab.com` to the DGX Spark's fixed IP on port 8080.

---

## Prerequisites (DGX Spark)

- Docker with [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)
- `huggingface-cli` available on the runner (`pip install huggingface_hub[cli]`)
- GitHub Actions self-hosted runner registered with label `dgx-spark`
- CUDA 12.8+ (required by vLLM for Blackwell/SM120)

---

## First-time setup

**1. Register the self-hosted runner on the DGX Spark:**

Go to your GitHub repo → Settings → Actions → Runners → New self-hosted runner.
Follow the instructions, and when prompted for labels add `dgx-spark`.

**3. Start the server manually for the first run:**
```bash
docker compose up -d
```
Both vLLM instances start loading their models (~21.9 GB and ~15 GB respectively). Watch progress:
```bash
docker compose logs -f vllm-35b vllm-27b
```

**4. Enable the systemd service so the stack starts on boot:**

Create `/etc/systemd/system/dgx-llm-server.service`:
```ini
[Unit]
Description=DGX LLM Server (vLLM + LiteLLM docker compose stack)
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

**5. Apply the K8s manifests:**
```bash
# Fill in the DGX Spark's actual fixed IP in k8s/external-service.yaml first
# Fill in your Traefik cert resolver name in k8s/ingress-route.yaml
kubectl apply -f k8s/
```

**6. Add `HF_TOKEN` as a GitHub Actions secret** (repo → Settings → Secrets and variables → Actions → New repository secret). Required to download models from HuggingFace.

**7. Trigger the first model download:**

Push any change to `models/models.json` or run the workflow manually from the Actions tab.

---

## Swapping or adding a model

1. Add the new model's entry to `models/models.json` (with `name` and `hf_repo`).
2. Add a new vLLM service entry in `compose.yaml`.
3. Add the new model to `litellm/config.yaml`.
4. Remove the old entries if replacing.
5. Push to `main`.

The GitHub Actions workflow runs on the DGX Spark, downloads the new NVFP4 repo (~15–22 GB), removes obsolete directories, and restarts the stack.

---

## Models

| Canonical name | Model | Format | Size | GPU mem utilization |
|---|---|---|---|---|
| `qwen3.6-35b-a3b` | Qwen3.6-35B-A3B | NVFP4 | ~21.9 GB | 0.50 |
| `qwen3.6-27b` | Qwen3.6-27B | NVFP4 | ~15 GB | 0.45 |

Context window: **262,144 tokens** for both. KV cache: FP8. Both models are resident in GPU memory simultaneously (~37 GB combined weights, ~91 GB remaining for KV cache).

---

## Client configuration

All clients use `https://dgx.blosshomelab.com` as the base URL. No authentication is required. Clients that require a non-empty API key field (like the OpenAI SDK) can pass any non-empty string.

**Claude Code / shell environment:**
```bash
export OPENAI_BASE_URL=https://dgx.blosshomelab.com/v1
export OPENAI_API_KEY=local
```

**Pi.dev (`~/.config/pi/models.json`):**
```json
{
  "providers": [{
    "name": "dgx-spark",
    "type": "openai",
    "baseUrl": "https://dgx.blosshomelab.com/v1",
    "apiKey": "local",
    "models": ["qwen3.6-35b-a3b", "qwen3.6-27b"]
  }]
}
```

**Python / K8s workloads:**
```python
from openai import OpenAI

client = OpenAI(
    base_url="https://dgx.blosshomelab.com/v1",
    api_key="local",
)
response = client.chat.completions.create(
    model="qwen3.6-35b-a3b",
    messages=[{"role": "user", "content": "Hello"}],
)
```

**Kubernetes pod environment variables:**
```yaml
env:
  - name: OPENAI_BASE_URL
    value: "https://dgx.blosshomelab.com/v1"
  - name: OPENAI_API_KEY
    value: "local"
```

---

## Verify GPU offload

After startup, confirm both vLLM instances loaded successfully:
```bash
docker compose logs vllm-35b | grep -i "GPU blocks\|model loaded\|error"
docker compose logs vllm-27b | grep -i "GPU blocks\|model loaded\|error"
```

The `GPU blocks` log line tells you the total KV cache capacity. Multiply by 16 (tokens per block) to get the maximum context tokens available.

---

## File reference

| File | Purpose |
|---|---|
| `compose.yaml` | Docker Compose: two vLLM instances + LiteLLM proxy |
| `litellm/config.yaml` | LiteLLM routing: maps canonical model names to vLLM backends |
| `models/models.json` | GitOps manifest: HuggingFace repo sources for each model |
| `.env` | API key (gitignored — copy from `.env.example`) |
| `k8s/external-service.yaml` | K8s Service + Endpoints pointing to DGX Spark IP |
| `k8s/ingress-route.yaml` | Traefik IngressRoute for `dgx.blosshomelab.com` |
| `.github/workflows/sync-models.yml` | GitOps workflow (runs on DGX Spark self-hosted runner) |
| `scripts/sync_models.py` | Downloads new NVFP4 repos, removes obsolete directories |
