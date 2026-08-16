# ADR 0010: Return to vLLM — single model, Qwen3.8-27B NVFP4

**Status:** Accepted
**Date:** 2026-08-15
**Supersedes:** ADR 0005

## Context

ADR 0005 moved to llama.cpp router mode specifically to keep `qwen3.6-35b-a3b` and `qwen3.8-27b` both resident and swappable by request name. In practice `qwen3.6-35b-a3b` saw far less use than expected, and llama.cpp's GGUF path is materially slower than vLLM on this hardware. The operator no longer needs the two-model swap capability router mode was built for, and unsloth published `unsloth/Qwen3.8-27B-NVFP4` - a native NVFP4 safetensors checkpoint that ships its own `model_mtp.safetensors` for MTP speculative decoding, closing the gap ADR 0005 hit (no NVFP4 GGUF existed for either model at the time).

## Decision

Drop `qwen3.6-35b-a3b` entirely and replace the llama.cpp router with a single vLLM container serving `unsloth/Qwen3.8-27B-NVFP4`:

- **Image:** `vllm/vllm-openai:nightly`
- **Model:** `unsloth/Qwen3.8-27B-NVFP4`, downloaded whole (no `allow_patterns` filter - it's a single-file safetensors repo, not a multi-quant GGUF repo)
- **Served model name:** `qwen3.8-27b` (`--served-model-name`) - kept short and stable rather than the full HF repo id, so clients don't need to change their `model` field on future checkpoint swaps
- **Port:** 8000 (reverts from 8080)
- **Key flags:** `--tensor-parallel-size 1`, `--gpu-memory-utilization 0.45`, `--max-model-len 262144`, `--max-num-seqs 4`, `--max-num-batched-tokens 8192`, `--enable-chunked-prefill`, `--enable-prefix-caching`, `--distributed-executor-backend mp`, `--speculative-config {"method":"mtp","num_speculative_tokens":5}` (uses the repo's bundled `model_mtp.safetensors`)
- **Reasoning parser:** `--reasoning-parser qwen3`
- **Tool call parser:** `--tool-call-parser qwen3_xml`, `--enable-auto-tool-choice`

`gpu-memory-utilization` is 0.45 rather than ADR 0004's 0.75 - this is a single dense 27B model, not the larger 35B-A3B MoE ADR 0004 sized for, and the operator supplied this value pre-tuned.

Remove `models/config.ini` and `models/chat-templates/*.jinja` (llama.cpp-only - vLLM reads `chat_template.jinja` bundled in the NVFP4 repo automatically, no separate template file needed). Simplify `models/models.json` to the single entry. `.github/workflows/sync-models.yml` no longer copies `config.ini`/`chat-templates` to the deploy host, and no longer triggers on those paths.

## Consequences

- Port changes from 8080 to 8000 - `k8s/external-service.yaml` and `k8s/ingress-route.yaml` updated. Clients don't need to change anything since they go through `https://dgx.blosshomelab.com` either way.
- No more model-swap-by-name - one model, all phases. `qwen3.6-35b-a3b` callers (previously `repowise`) must be repointed at `qwen3.8-27b`.
- ADR 0003's `?model=` scrape constraint no longer applies - it was a llama.cpp router-mode bug, not a vLLM behavior. `k8s/service-monitor.yaml` re-enabled with a normal scrape config.
- `--distributed-executor-backend mp` and the MTP `--speculative-config` are both new territory for this stack under vLLM - ADR 0004's config never used either. Watch startup logs for the MTP draft model loading correctly and for OOMs at `gpu-memory-utilization 0.45` before trusting this as final.
- `scripts/sync_models.py` needs no changes - it already downloads whole repos when `allow_patterns` is omitted from the manifest entry, and its GGUF-file cleanup pass is harmless no-op for a safetensors repo.
