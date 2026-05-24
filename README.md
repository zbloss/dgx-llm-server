# dgx-llm-server

Serves quantized LLMs from a DGX Spark over the local network via an OpenAI-compatible API. Models are managed through git — push a config change to swap or add a model without touching the DGX Spark.

**Endpoint:** `https://dgx.blosshomelab.com`

---

## How it works

- **llama.cpp server** runs in router mode: no model is pre-loaded. When a client sends `"model": "qwen3-coder"`, the server loads that model on demand and evicts the previous one (only one model runs at a time).
- **`models/config.ini`** maps canonical model names to GGUF file paths and per-model parameters.
- **`models/models.json`** is the GitOps manifest: push a change here and the self-hosted GitHub Actions runner on the DGX Spark downloads new files, removes obsolete ones, and restarts the server.
- **Traefik** in the homelab K8s cluster terminates TLS and routes `dgx.blosshomelab.com` to the DGX Spark's fixed IP on port 8080.

---

## Prerequisites (DGX Spark)

- Docker with [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)
- `huggingface-cli` available on the runner (`pip install huggingface_hub[cli]`)
- GitHub Actions self-hosted runner registered with label `dgx-spark`

---

## First-time setup

**1. Copy `.env` and set your API key:**
```bash
cp .env.example .env
# Edit .env and set LLAMA_API_KEY to a strong random string
```

**2. Register the self-hosted runner on the DGX Spark:**

Go to your GitHub repo → Settings → Actions → Runners → New self-hosted runner.
Follow the instructions, and when prompted for labels add `dgx-spark`.

**3. Start the server manually for the first run:**
```bash
docker compose up -d
```
The server starts but no model is loaded yet — models are downloaded by the first GitHub Actions run or on first client request (if files are already on disk).

**4. Apply the K8s manifests:**
```bash
# Fill in the DGX Spark's actual fixed IP in k8s/external-service.yaml first
# Fill in your Traefik cert resolver name in k8s/ingress-route.yaml
kubectl apply -f k8s/
```

**5. Add your HuggingFace token as a GitHub Actions secret** (required to download models):

Repo → Settings → Secrets and variables → Actions → New repository secret → `HF_TOKEN`

**6. Trigger the first model download:**

Push any change to `models/models.json` or run the workflow manually from the Actions tab.

---

## Swapping or adding a model

1. Add the new model's entry to `models/models.json` and `models/config.ini`.
2. Remove the old entry if replacing (the sync script will delete the old `.gguf`).
3. Push to `main`.

The GitHub Actions workflow runs on the DGX Spark, downloads the new file (~65 GB for the 80B model), removes obsolete files, and restarts the server. Watch progress in the Actions tab.

---

## Models

| Canonical name | Model | Quant | GGUF size |
|---|---|---|---|
| `qwen3.6-35b-a3b-mtp` | Qwen3.6-35B-A3B MTP | Q8_0 | ~37 GB |
| `qwen3.6-27b-mtp` | Qwen3.6-27B MTP | Q8_0 | ~29 GB |

Context window: **262,144 tokens** for both. Full GPU offload (`-ngl 99`).

---

## Client configuration

All clients use `https://dgx.blosshomelab.com` as the base URL and the `LLAMA_API_KEY` value from `.env` as the API key.

**Claude Code / shell environment:**
```bash
export OPENAI_BASE_URL=https://dgx.blosshomelab.com/v1
export OPENAI_API_KEY=your-api-key
```

**Pi.dev (`~/.config/pi/models.json`):**
```json
{
  "providers": [{
    "name": "dgx-spark",
    "type": "openai",
    "baseUrl": "https://dgx.blosshomelab.com/v1",
    "apiKey": "your-api-key",
    "models": ["qwen3.6-35b-a3b-mtp", "qwen3.6-27b-mtp"]
  }]
}
```

**Python / K8s workloads:**
```python
from openai import OpenAI

client = OpenAI(
    base_url="https://dgx.blosshomelab.com/v1",
    api_key="your-api-key",
)
response = client.chat.completions.create(
    model="qwen3.6-35b-a3b-mtp",
    messages=[{"role": "user", "content": "Hello"}],
)
```

**Kubernetes pod environment variables:**
```yaml
env:
  - name: OPENAI_BASE_URL
    value: "https://dgx.blosshomelab.com/v1"
  - name: OPENAI_API_KEY
    valueFrom:
      secretKeyRef:
        name: dgx-llm-api-key
        key: key
```

---

## Verify GPU offload

On first run, confirm all layers are on the GPU:
```bash
docker compose logs -f | grep -i "gpu\|layer\|offload"
```

---

## File reference

| File | Purpose |
|---|---|
| `compose.yaml` | Docker Compose for the llama.cpp server |
| `models/config.ini` | llama.cpp model presets (canonical names, paths, params) |
| `models/models.json` | GitOps manifest (HuggingFace download sources) |
| `.env` | API key (gitignored — copy from `.env.example`) |
| `k8s/external-service.yaml` | K8s Service + Endpoints pointing to DGX Spark IP |
| `k8s/ingress-route.yaml` | Traefik IngressRoute for `dgx.blosshomelab.com` |
| `.github/workflows/sync-models.yml` | GitOps workflow (runs on DGX Spark self-hosted runner) |
| `scripts/sync_models.py` | Downloads new models, removes obsolete ones |
