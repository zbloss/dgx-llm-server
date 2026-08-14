# dgx-llm-server

Serves quantized LLMs from a DGX Spark over the local network via an OpenAI-compatible API. Models are managed through git — push a config change to swap or add a model without touching the DGX Spark.

**Endpoint:** `https://dgx.blosshomelab.com`

---

## How it works

- **A single llama.cpp server** (`llama-server`, router mode) holds a roster of GGUF model profiles defined in `models/config.ini`. Only one model is resident in GPU memory at a time (`--models-max 1`); the router transparently unloads the current model and loads whichever one a request names in its `model` field.
- **`models/models.json`** is the GitOps manifest: push a change here and the self-hosted GitHub Actions runner on the DGX Spark downloads new GGUF repos, removes obsolete ones, and restarts the stack.
- **`models/config.ini`** is the router preset file: add, remove, or retune a model profile here — no `compose.yaml` change needed unless a GPU-level flag changes.
- **Traefik** in the homelab K8s cluster terminates TLS and routes `dgx.blosshomelab.com` to the DGX Spark's fixed IP on port 8080. The manifests that actually do this live in the `home-server` GitOps repo (`kubernetes/apps/ml/dgx-llama-cpp/`, Flux-managed) — `k8s/` in *this* repo is an illustrative example only, kept for reference, not applied anywhere.
- The endpoint has no API key auth — access is scoped by network/Traefik routing, not by a bearer token.

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
The default-warm model (`qwen3.6-35b-a3b`, `load-on-startup = true` in `models/config.ini`) starts loading immediately. Watch progress:
```bash
docker compose logs -f llama-server
```
Other profiles in `models/config.ini` load lazily on first request.

**3. Enable the systemd service so the stack starts on boot:**

Create `/etc/systemd/system/dgx-llm-server.service`:
```ini
[Unit]
Description=DGX LLM Server (llama.cpp docker compose stack)
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

The manifests that actually route `dgx.blosshomelab.com` live in the `home-server` GitOps repo (`kubernetes/apps/ml/dgx-llama-cpp/`), applied automatically by Flux. `k8s/` in *this* repo is an illustrative example only — useful as a reference for what the real ones look like, but not something you `kubectl apply` here. To change routing, DGX Spark IP, or Prometheus scraping, edit the files in `home-server` instead.

**5. Add `HF_TOKEN` as a GitHub Actions secret** (repo → Settings → Secrets and variables → Actions → New repository secret). Required to download models from HuggingFace during the GitOps sync — the `llama-server` container itself never talks to HuggingFace.

**6. Trigger the first model download:**

Push any change to `models/models.json`, `models/config.ini`, or `compose.yaml`, or run the workflow manually from the Actions tab.

---

## Swapping or adding a model

1. Add the new model's entry to `models/models.json` (`name`, `hf_repo`, and an `allow_patterns` filter — GGUF repos ship many quant variants, and you almost always want exactly one plus the `mmproj` file, not the whole repo).
2. Add a matching `[profile-name]` section to `models/config.ini` pointing at the downloaded file paths.
3. Remove the old entries from both files if replacing rather than adding.
4. Push to `main`.

The GitHub Actions workflow runs on the DGX Spark, downloads the new GGUF file(s), removes obsolete directories, and restarts the stack. Clients then select the new model by putting its `config.ini` profile name in the `model` field — the router loads it on first request, no further deploy needed. See `docs/adr/0005-return-to-llama-cpp-router-mode.md` for the reasoning behind the current model-swap design and its VRAM/context tradeoffs.

---

## Models

| Profile (models/config.ini) | Model | Format | GGUF file | GPU |
|---|---|---|---|---|
| `qwen3.6-35b-a3b` | Qwen3.6-35B-A3B (MoE) | GGUF, UD-Q4_K_XL | `unsloth/Qwen3.6-35B-A3B-GGUF` | full offload |
| `qwen3.8-27b` | Qwen3.8-27B (dense) | GGUF, UD-Q4_K_XL | `unsloth/Qwen3.8-27B-GGUF` | full offload |

Both profiles: 524,288-token total context budget split across 2 parallel slots (262,144 tokens/slot), MTP speculative decoding, native vision via `mmproj`, q8_0-quantized KV cache. Only one profile is loaded in GPU memory at a time (`--models-max 1`); `qwen3.6-35b-a3b` loads at container startup, `qwen3.8-27b` loads on first request. See the ADR for why these specific numbers were chosen (they're derived from a prior OOM fix, not guessed).

---

## Client configuration

All clients use `https://dgx.blosshomelab.com/v1` as the base URL and a `config.ini` profile name (`qwen3.6-35b-a3b` or `qwen3.8-27b`) in the `model` field. No API key is required.

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
    "models": ["qwen3.6-35b-a3b", "qwen3.8-27b"]
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
    value: "unused"
```

---

## Verify GPU offload

After startup, confirm the default-warm model loaded successfully:
```bash
docker compose logs llama-server | grep -i "loaded\|error"
curl -s http://localhost:8080/health
curl -s http://localhost:8080/v1/models | jq .
```

`GET /health` and `GET /v1/models` never trigger a model load, so they're safe to poll or use in healthchecks — unlike a chat completion against a specific `model` name, which will force-load (and, with `--models-max 1`, evict whatever else is loaded).

---

## File reference

| File | Purpose |
|---|---|
| `compose.yaml` | Docker Compose: single `llama-server` container in router mode |
| `models/config.ini` | Router preset: per-model GPU/context/speculative-decoding settings |
| `models/chat-templates/*.jinja` | Per-model chat templates, patched from the upstream GGUF-embedded ones — see `docs/adr/0006-patch-chat-templates-for-agentic-clients.md` |
| `models/models.json` | GitOps manifest: HuggingFace repo + quant filter for each model |
| `k8s/*.yaml` | Illustrative examples only — not applied anywhere. The real manifests (Service, IngressRoute/HTTPRoute, ServiceMonitor) live in `home-server`'s `kubernetes/apps/ml/dgx-llama-cpp/`, Flux-managed. Prometheus scraping is currently disabled there — see the warning comment in that repo's `servicemonitor.yaml` before re-enabling it. |
| `.github/workflows/sync-models.yml` | GitOps workflow (runs on DGX Spark self-hosted runner) |
| `scripts/sync_models.py` | Downloads new GGUF repos (filtered by `allow_patterns`), removes obsolete ones |
| `docs/adr/` | Architecture decision records for the model stack's history |
