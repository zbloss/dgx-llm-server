# ADR 0003: Prometheus scraping without model query parameter

**Status:** Accepted  
**Date:** 2026-06-05

## Context

The llama.cpp Server exposes a Prometheus-compatible `/metrics` endpoint. This endpoint accepts an optional `?model=<name>` query parameter. A ServiceMonitor was configured with two separate scrape endpoints — one per Model Profile — each passing a different `model=` param at a 30-second interval.

This configuration caused a critical failure mode: any Prometheus scrape with `?model=qwen3.6-35b-a3b-mtp` is treated by llama-serve as a model load request, identical to an agent inference request. This triggered a forced unload of the active 27B model and a load attempt of the 35B — every 30 seconds. The 35B load would then be force-killed when any in-flight 27B agent request arrived, the 27B reloaded, and the cycle repeated. Over 2,100 model load events were observed in a single container lifetime, each incurring 11–16 seconds of downtime during which all agent requests timed out.

The `?model=` parameter on `/metrics` is not a filter — it is a model activation trigger.

## Decision

Scrape `/metrics` without any `?model=` query parameter. The endpoint returns complete server-level metrics (throughput, KV cache utilization, slot occupancy, token rates) regardless of which model is active. Per-model label cardinality is handled at the scrape level using a static `model` relabeling pointing to the currently-active model profile.

When the active model changes (e.g. switching to `qwen3.6-35b-a3b-mtp`), the ServiceMonitor relabeling is updated to match. Metrics gaps during a model switch are acceptable — they reflect a genuine operational state change, not a data loss.

## Alternatives considered

**Two scrape targets, one per model profile:** Causes forced 30-second model cycling regardless of agent activity. Rejected — this is the failure mode described above.

**Single scrape target with `?model=<active>`:** Functionally correct, but the `?model=` param is not needed to get metrics. Including it creates a latent footgun: if the param is ever updated to a non-active model name, the swap trigger fires again. Rejected in favor of removing the param entirely.

**Custom sidecar exporter that reads `/v1/models` and re-exports metrics:** Architecturally correct but unnecessary complexity for a homelab setup. The `/v1/models` health endpoint can be scraped separately (it does not trigger model loads) if per-model load status is needed. Deferred.

## Consequences

- Prometheus scraping no longer triggers model loads. The swap cycle stops immediately.
- Global server metrics (latency, throughput, KV cache fill rate) are available at all times regardless of active model.
- Per-model differentiation in Grafana relies on the static relabeling label, which must be kept in sync with the active model manually when switching.
- The `?model=` parameter must never be added to Prometheus scrape configs for this server. This constraint must be documented in the ServiceMonitor itself via a comment.
