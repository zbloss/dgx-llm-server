# ADR-0002: LiteLLM Proxy for Multi-Model Routing

## Status
Accepted

## Context

vLLM serves exactly one model per process. The previous llama.cpp setup used its built-in router mode (`--models-preset`, `--models-max`) to read the `model` field from incoming requests and load the appropriate model on demand — no external routing component needed.

After migrating to vLLM, something must route `model: "qwen3.6-35b-a3b"` to one vLLM instance and `model: "qwen3.6-27b"` to another, while presenting a single OpenAI-compatible endpoint to all clients.

Candidates evaluated:
- **LiteLLM proxy**: OpenAI-compatible proxy with config-driven routing to named backends, built-in health checking, and a mature operational story.
- **Custom FastAPI router**: thin Python app routing by `model` field; simple but requires owning all routing logic, error handling, and health checking.
- **Traefik (existing ingress)**: cannot inspect JSON request bodies to read the `model` field without complex middleware plugins.

## Decision

Use LiteLLM proxy as the single client-facing endpoint (port 8080), routing by `model` field to the appropriate vLLM instance on an internal port.

LiteLLM runs as a Docker Compose service alongside the two vLLM instances. Its config (`litellm/config.yaml`) is version-controlled and maps canonical model names to backend URLs.

## Consequences

- **All clients continue to use the same base URL** (`https://dgx.blosshomelab.com`) and API key — no client changes required for the routing layer itself.
- **Canonical model name changes** (`qwen3.6-35b-a3b-mtp` → `qwen3.6-35b-a3b`, `qwen3.6-27b-mtp` → `qwen3.6-27b`) require a one-time client update, but this is a separate concern from the proxy choice.
- **Adding a new model** requires updating both `models/models.json` (for GitOps download) and `litellm/config.yaml` (for routing), then restarting the stack.
- **LiteLLM is a dependency**: if the LiteLLM process is unhealthy, all model traffic is blocked even if vLLM instances are healthy. The tradeoff was accepted over a custom router because LiteLLM's health-checking and error-handling behavior is well-tested.
