# dgx-llm-server

Serves a quantized LLM from a DGX Spark over the local network via an OpenAI-compatible API. Models are managed through git - push a config change to swap the model without touching the DGX Spark.

**Endpoint:** `https://dgx.blosshomelab.com`

---

## How it works

- **The gate** (`gate/gate.py`, ADR 0019) is the front door on port 8000. It proxies everything to **llama-swap** on `127.0.0.1:8080` (WebSocket upgrades are tunnelled, for ComfyUI's UI), except while a **Window hold** is active: then LLM requests get a fast `503` with `Retry-After` so a nightly Generation window can have the GPU. The hold's admin API listens on `127.0.0.1:8001` only.
- **llama-swap** owns the lifecycle of every service under `profiles: [managed]` in `compose.yaml`, launching them through the Docker socket. Today that is one model, `qwen3.8-flash-next` (SGLang, `lmsysorg/sglang:dev-qwen38-next-local`, on `127.0.0.1:30000`), preloaded on boot. If it dies (a crash, or `mem-watchdog` killing it), the next request relaunches it.
- **`models/models.json`** is the GitOps manifest: push a change here (or to `compose.yaml`, `gate/gate.py`, `llama-swap/`) and the self-hosted GitHub Actions runner on the DGX Spark waits out any Window hold, downloads the new HuggingFace repo, removes the obsolete one, and applies the stack.
- **`compose.yaml`** carries the SGLang launch flags (context length, batching, PLE-table NVMe offload, tool/reasoning parsers) - edit it directly to retune the model. The container's `entrypoint` is overridden to delete and repopulate the ~47.7GB PLE table on every start (see `docs/adr/0016-qwen38-flash-next-sglang-nvme-ple.md`), so expect a 10-15 minute startup window, not a few seconds.
- **Traefik** in the homelab K8s cluster terminates TLS and routes `dgx.blosshomelab.com` to the DGX Spark's fixed IP on port 8000. The manifests that actually do this live in the `home-server` GitOps repo (`kubernetes/apps/ml/dgx-llama-cpp/`, Flux-managed); this repo carries no Kubernetes manifests.
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
This starts the gate, llama-swap and mem-watchdog; llama-swap then preloads `sglang-server`. Watch progress as the model loads (expect 10-15 minutes while the PLE table repopulates):
```bash
docker compose logs -f llama-swap
```

**3. Enable the systemd service so the stack starts on boot:**

Create `/etc/systemd/system/dgx-llm-server.service`:
```ini
[Unit]
Description=DGX LLM Server (SGLang docker compose stack)
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

The manifests that actually route `dgx.blosshomelab.com` live in the `home-server` GitOps repo (`kubernetes/apps/ml/dgx-llama-cpp/`), applied automatically by Flux. To change routing, DGX Spark IP, or Prometheus scraping, edit the files in `home-server` instead.

**5. Add `HF_TOKEN` as a GitHub Actions secret** (repo → Settings → Secrets and variables → Actions → New repository secret). Required to download the model from HuggingFace during the GitOps sync - the `sglang-server` container itself never talks to HuggingFace (`HF_HUB_OFFLINE=1`).

**6. Trigger the first model download:**

Push any change to `models/models.json`, `compose.yaml`, `gate/gate.py` or `llama-swap/`, or run the workflow manually from the Actions tab.

---

## Swapping the model

1. Update the entry in `models/models.json` (`name`, `hf_repo`, and an optional `allow_patterns` filter if the new repo publishes multiple quant variants and you only want one).
2. Update `compose.yaml`'s `--model-path` / `--served-model-name` to match the new local path and repo id.
3. Push to `main`.

The GitHub Actions workflow runs on the DGX Spark, waits out any active Window hold, downloads the new model, removes the obsolete directory, applies the stack, and - if `sglang-server`'s definition changed - unloads and re-warms the model through llama-swap.

---

## Model

| Served model name | Model | Format | HF repo | GPU |
|---|---|---|---|---|
| `qwen3.8-flash-next` | Qwen3.8-Flash-Next (176B/6B-active hybrid MoE) | NVFP4 safetensors | `RadixArk/Qwen3.8-Flash-Next-NVFP4` | full offload, tensor-parallel-size 1, PLE table offloaded to local NVMe |

No speculative decoding in this first pass (SGLang's MTP-equivalent, `--speculative-algorithm NEXTN`, has unverified required sub-flags), `auto`-resolved reasoning/tool-call parsers, `mem-fraction-static 0.85`, 8-request concurrency ceiling (`--max-running-requests 8`). Replaces the prior `qwen3.8-27b`/vLLM stack entirely - the model doesn't fit in 128GB unified memory alongside anything else. See `docs/adr/0016-qwen38-flash-next-sglang-nvme-ple.md` for the full reasoning, including why this needed a backend switch (`docs/adr/0013-dflash2-speculative-decoding.md` covers the earlier, narrower SGLang-migration question this revisits) and prior `qwen3.8-27b`-era history (`docs/adr/0010`-`0015`).

---

## Client configuration

All clients use `https://dgx.blosshomelab.com/v1` as the base URL and `qwen3.8-flash-next` in the `model` field. No API key is required.

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
    "models": ["qwen3.8-flash-next"]
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
    model="qwen3.8-flash-next",
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
docker compose logs llama-swap | grep -i "error\|loaded weights"
curl -s http://localhost:8000/running | jq .
curl -s http://localhost:8000/v1/models | jq .
```

## Window hold

The nightly Generation window (ai-grifting) opens a hold before it takes the GPU, renews it by heartbeat, and releases it when done. Unrenewed, it lapses after 30 minutes. From the Spark only:
```bash
curl -s -X POST   http://127.0.0.1:8001/hold   # open (or renew)
curl -s -X PUT    http://127.0.0.1:8001/hold   # renew - also re-opens a hold a gate restart dropped
curl -s           http://127.0.0.1:8001/hold   # {"active": ..., "expires_in": seconds}
curl -s -X DELETE http://127.0.0.1:8001/hold   # release
```
While it is active, only `/v1/models`, `/models`, `/health`, `/metrics`, `/running` and generation-group models get through the gate; everything else gets a `503`. Processes on the Spark that must not be blocked call llama-swap on `127.0.0.1:8080` directly.

---

## File reference

| File | Purpose |
|---|---|
| `compose.yaml` | Docker Compose: the gate, llama-swap, mem-watchdog, and the llama-swap-managed `sglang-server` (all SGLang launch flags) |
| `gate/gate.py` | The front door on :8000 and the Window hold admin API (stdlib only; tests in `tests/`) |
| `llama-swap/config.yaml` | llama-swap models, groups, boot preload; reloaded on change |
| `llama-swap/Dockerfile` | llama-swap on the `docker:27-cli` image, so it can drive compose |
| `models/models.json` | GitOps manifest: HuggingFace repo (and optional quant filter) for the model |
| `.github/workflows/sync-models.yml` | GitOps workflow (runs on DGX Spark self-hosted runner) |
| `scripts/sync_models.py` | Downloads the model repo (filtered by `allow_patterns` if set), removes obsolete ones |
| `scripts/benchmark.py` | Lightweight single-stream benchmark (TTFT/TTFAT/tok-per-sec) for the repo's three fixed prompt profiles |
| `guidellm/` | Dockerized [guidellm](https://github.com/vllm-project/guidellm) benchmark - concurrency sweeps, latency percentiles. See below. |
| `docs/adr/` | Architecture decision records for the model stack's history |

---

## Benchmarking with guidellm

`guidellm` depends on `uvloop`, which doesn't build on Windows, so it runs in Docker - this also keeps the load generator off the same machine as the server under test, which running it directly on the Spark would not.

```bash
./guidellm/run.sh [duration_seconds]   # default 30s per sweep step
```

This builds the image (if needed), runs a `sweep` profile (synchronous -> throughput -> ramping constant-rate steps) against `qwen3.8-flash-next` on the Spark, and writes timestamped results to `guidellm/results/<model>_<YYYYmmdd-HHMMSS>.{json,csv}` for tracking performance over time across config changes. Override `TARGET`, `TOKENIZER_MODEL`, or `MODEL_NAME` env vars to point at a different server or model.
