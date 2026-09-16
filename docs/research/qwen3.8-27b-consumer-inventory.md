# `qwen3.8-27b` consumer inventory

Resolves #35 (part of map #28). Discovery only — nothing outside `dgx-llm-server`
was modified to produce this inventory, and this document does not itself change
any consumer.

**Date:** 2026-09-16
**Search terms:** `qwen3.8-27b` (served model name), `unsloth/Qwen3.8-27B-NVFP4`
(HF checkpoint id), `dgx.blosshomelab.com` (serving endpoint) — case-insensitive,
plus obvious variants (`Qwen3.8-27B` capitalization, `Qwen3-27b` without the
`.8`, `openai/qwen3.8-27b` provider-prefixed form).

Every location below was actually searched. Where a location has no hits, that
is stated explicitly rather than omitted, so this inventory can be read as
complete rather than as "here's what turned up."

---

## 1. `home-server` (GitOps repo, `C:\Users\altoz\Projects\home-server`)

### 1.1 Directory-naming discrepancy (context for the rest of this section)

`dgx-llm-server`'s own README/ticket #35 point at
`kubernetes/apps/ml/dgx-vllm/` for the "real" routing manifests. **No such
directory exists.** The actual directory is
`kubernetes/apps/ml/dgx-llama-cpp/`, which its own `servicemonitor.yaml`
comment (see 1.2) says is "historically named after llama-cpp but actually
serving vLLM" — i.e. the folder name predates two model-stack migrations
(llama.cpp → vLLM → now SGLang) and was never renamed. Whoever migrates
consumers should be aware the resource/folder name `dgx-llama-cpp` is what
Flux actually applies, not `dgx-vllm`.

### 1.2 Live routing manifests — `kubernetes/apps/ml/dgx-llama-cpp/`

| File | Line | Reference | Type |
|---|---|---|---|
| `servicemonitor.yaml` | 2 | comment: `ServiceMonitor for external DGX vLLM server at 192.168.68.104:8000` | endpoint (IP), contextual |
| `servicemonitor.yaml` | 6 | comment: `unsloth/Qwen3.8-27B-NVFP4` | checkpoint id (explanatory comment only, not a live selector/param) |
| `httproute.yaml` | 12 | `dgx.blosshomelab.com` (hostname) | endpoint |
| `external-service.yaml` | — | **zero hits** — only IP `192.168.68.104` and port `8000`, no model name/checkpoint text | n/a |
| `kustomization.yaml` | — | **zero hits** | n/a |

**Prometheus `?model=` param check (per ticket's CONTEXT.md pointer):** `servicemonitor.yaml`'s
`endpoints:` block (lines 22-25) scrapes `path: /metrics` with **no `params:`
block and no `?model=` query string**. The file's own comment (lines 4-10)
explains this was deliberately removed: the `?model=` requirement was a
llama.cpp-router-mode-specific quirk (dgx-llm-server ADR 0003) that doesn't
apply to vLLM, so it's "safe to scrape on a normal interval now." **This is
still accurate for the vLLM/`qwen3.8-27b` era it describes; it has not been
re-verified against the new SGLang/`qwen3.8-flash-next` stack** (no
`home-server` file was found that mentions SGLang, PLE, or
`qwen3.8-flash-next` — see 1.4). If SGLang's `/metrics` shape differs from
vLLM's, this ServiceMonitor may need a fresh look at migration time, though
today it contains no `qwen3.8-27b`-specific text that would need editing —
only the explanatory comment on line 6.

### 1.3 Client HelmReleases / ConfigMaps referencing the model name and/or endpoint

| File | Line | Reference | Type |
|---|---|---|---|
| `kubernetes/apps/any-given-sundai/helmrelease.yaml` | 73 | `LLM_BASE_URL: "https://dgx.blosshomelab.com/v1"` | endpoint |
| `kubernetes/apps/any-given-sundai/helmrelease.yaml` | 74 | `LLM_MODEL: "qwen3.8-27b"` | model name |
| `kubernetes/apps/hermes/helmrelease.yaml` | 44 | `OPENAI_BASE_URL: "https://dgx.blosshomelab.com/v1"` | endpoint |
| `kubernetes/apps/hermes/hermes-config-configmap.yaml` | 19 | `default: qwen3.8-27b` | model name |
| `kubernetes/apps/hermes/hermes-config-configmap.yaml` | 21 | `base_url: https://dgx.blosshomelab.com/v1` | endpoint |
| `kubernetes/apps/documents/paperless-gpt/helmrelease.yaml` | 44 | `LLM_MODEL: "qwen3.8-27b"` | model name |
| `kubernetes/apps/documents/paperless-gpt/helmrelease.yaml` | 45 | `OPENAI_BASE_URL: "https://dgx.blosshomelab.com/v1"` | endpoint |
| `kubernetes/apps/agents/devloop/helmrelease.yaml` | 85 | `model: "openai/qwen3.8-27b"` | model name (provider-prefixed variant) |
| `kubernetes/apps/agents/devloop/helmrelease.yaml` | 86 | `baseUrl: "http://dgx-llama-cpp.ml.svc.cluster.local:8000/v1"` | endpoint — **note:** in-cluster Service DNS, not the public `dgx.blosshomelab.com` hostname; still points at the same backend |

### 1.4 `home-server` `CONTEXT.md` and ADRs

| File | Line | Reference | Type |
|---|---|---|---|
| `docs/adr/0001-hermes-agent-deployment.md` | 18 | `https://dgx.blosshomelab.com/v1`, `nvidia/Qwen3.6-35B-A3B-NVFP4` | endpoint hit; **model text is `Qwen3.6-35B-A3B`, not `qwen3.8-27b`** — this ADR predates the `qwen3.8-27b` era and is already stale documentation, included here only because it matches the endpoint term |
| `CONTEXT.md` | 119-121 | "DGX vLLM endpoint" glossary entry: `https://dgx.blosshomelab.com/v1`, `192.168.68.104:8000`, `kubernetes/apps/ml/dgx-llama-cpp/` | endpoint; **model text says `nvidia/Qwen3.6-35B-A3B-NVFP4`, not `qwen3.8-27b`** — same as above, this glossary entry is already out of date relative to `dgx-llm-server`'s own history and was never updated through the `qwen3.8-27b` era, let alone to `qwen3.8-flash-next` |

No occurrence of `qwen3.8-27b` or `unsloth/Qwen3.8-27B-NVFP4` verbatim was found anywhere in `home-server`'s `CONTEXT.md` or `docs/adr/` — only the endpoint hostname, attached to an even older model reference. **This means `home-server`'s own `CONTEXT.md` is already out of sync with `dgx-llm-server`'s model history and will need attention independent of this migration.**

### 1.5 Locations searched with zero hits

- `kubernetes/apps/ml/dgx-llama-cpp/external-service.yaml`, `kustomization.yaml`
- Full-repo grep for `qwen3.8-27b` / `Qwen3.8-27B-NVFP4` (case-insensitive) outside the files listed above — no other HelmReleases, Kustomizations, IngressRoutes, or values files reference the model name or checkpoint id.

---

## 2. Home directory (`C:\Users\altoz`)

### 2.1 `~/.config/pi/` (pi.dev)

**The directory does not exist on this machine.** `dgx-llm-server`'s README
shows an example `~/.config/pi/models.json` referencing `qwen3.8-flash-next`
(previously `qwen3.8-27b`), but no pi.dev config is actually present locally
to search — zero hits because there is nothing there.

### 2.2 Qwen Code CLI (`~/.qwen/`)

| File | Line | Reference | Type |
|---|---|---|---|
| `~/.qwen/settings.json` | 12 | `"id": "qwen3.8-27b"` | model name |
| `~/.qwen/settings.json` | 13 | `"name": "qwen3.8-27b"` | model name |
| `~/.qwen/settings.json` | 14 | `"baseUrl": "https://dgx.blosshomelab.com/v1"` | endpoint |
| `~/.qwen/settings.json` | 31 | `"baseUrl": "https://dgx.blosshomelab.com/v1"` | endpoint (second provider entry, `qwen3.6-35b-a3b`, same base URL) |
| `~/.qwen/settings.json` | 53 | `"model": { "name": "qwen3.8-27B" }` | model name — **capitalization variant** (`27B` vs `27b`), this is the CLI's currently-selected default model |

The env var key name itself also encodes the endpoint:
`QWEN_CUSTOM_API_KEY_OPENAI_HTTPS_DGX_BLOSSHOMELAB_COM_V1_87CFCEF8DFC1` (line 8) — not a separate reference to migrate, just a generated key name derived from the base URL.

`~/.config/configstore/update-notifier-@qwen-code/qwen-code.json` — searched, zero hits (only update-check metadata).

### 2.3 `~/.claude/` (Claude Code)

| File | Line | Reference | Type |
|---|---|---|---|
| `~/.claude/settings.json` | 124 | `"**Trusted internal domains**: any-given-sundai.blosshomelab.com, dgx.blosshomelab.com, blosshomelab.com"` | endpoint (cached project-environment text for the `any-given-sundai` project) |
| `~/.claude/settings.json` | 126 | `"**Key internal services**: any-given-sundai.blosshomelab.com (app host), dgx.blosshomelab.com"` | endpoint (same cached block) |

No model-name (`qwen3.8-27b`) text appears in `~/.claude/settings.json` — only the endpoint hostname, inside a cached "environment" summary for the `any-given-sundai` project (not live routing config; would regenerate on its own next time that project's context is refreshed).

`~/.claude/CLAUDE.md` — searched, zero hits (contains only the Python/`uv` instruction, unrelated to this topic).

No MCP server config files were found under `~/.claude/` (no `*mcp*` files), and no `~/.mcp.json` exists in the home directory.

`~/.claude.json` (Claude Code's cross-project state file) — two hits, both **usage telemetry, not live configuration**:

| File | Line | Reference | Type |
|---|---|---|---|
| `~/.claude.json` | 1429 | `"qwen3.8-27b": { "inputTokens": ... }` under `lastModelUsage` for a project | model name — historical cost/usage record only |
| `~/.claude.json` | 2615 | `"qwen3.8-27b": { "inputTokens": ... }` under `lastModelUsage` for another project | model name — historical cost/usage record only |

These are per-project usage-stat caches Claude Code writes after a session; they don't drive any client behavior and don't need updating for a migration to take effect (they'll simply start recording `qwen3.8-flash-next` once that model is in use).

`~/.claude.json.backup` — searched, zero hits.

### 2.4 Locations searched with zero hits (home directory)

- `~/.config/pi/` — directory absent
- `~/.config/` recursive search for `*qwen*` filenames — only the qwen-code update-notifier file (2.2, zero content hits)
- `~/.claude/CLAUDE.md`
- `~/.claude.json.backup`
- Any MCP server config under `~/.claude/` or `~/.mcp.json` — none exist

---

## 3. Other repos under `C:\Users\altoz\Projects\*`

All directories under `Projects/` (excluding `dgx-llm-server` and `home-server`,
already covered above) were grepped for the three terms and variants,
excluding `.git`, `node_modules`, `.venv`/`venv`, `dist`, `build`.

Repos list searched: `TRELLIS.2`, `any-given-sundai`, `arc-raiders`, `candle`,
`crypto-tax-tracker`, `devloop`, `fantasy-football-bot`, `finances`,
`fire-calculator`, `golf-sim`, `greenhouse-interview`, `learn-go`,
`local-agent-company`, `local-llm`, `minto`, `new-claude-skills`, `omneval`,
`omneval-pr286`, `omneval-pr307`, `omneval-prwork`, `paperclip`,
`the-guide`, `transformers`, `website`,
`would-you-believe-i-am-learning-rust-again`, `zachbloss-website`
(plus loose files `local-claude.bat`, `prompt.txt`,
`skills-to-create-prd.md` directly under `Projects/`).

### 3.1 `any-given-sundai`

| File | Line | Reference | Type |
|---|---|---|---|
| `.env` | 3 | `LLM_BASE_URL=https://dgx.blosshomelab.com/v1` | endpoint |
| `.repowise/.env` | 3 | `ANTHROPIC_BASE_URL=https://dgx.blosshomelab.com` | endpoint |
| `.repowise/jobs/5399b590-72f8-41fd-9767-34890a97519e.json` | 44 | `"model_name": "qwen3.8-27b"` | model name (repowise job record — historical run metadata) |
| `.repowise/jobs/ffb95013-c7e3-4936-b1a8-2cf9754ee23a.json` | 198 | `"model_name": "qwen3.8-27b"` | model name (repowise job record — historical run metadata) |

### 3.2 `devloop`

| File | Line | Reference | Type |
|---|---|---|---|
| `agents/.env` | 7 | `AGENT_LLM_BASE_URL="https://dgx.blosshomelab.com/v1"` | endpoint |

### 3.3 `omneval` and its PR/worktree copies

`CONTEXT.md` in these repos contains a "Qwen3-27b" glossary entry — **note the
variant spelling: `Qwen3-27b`, missing the `.8` that `dgx-llm-server` uses
(`qwen3.8-27b`)**. Same endpoint, same underlying box, imprecise model-name
text. Also describes it as served "via `llama.cpp`", which is stale even for
the `qwen3.8-27b`/vLLM era, let alalone the current SGLang stack — this
documentation has not tracked any of `dgx-llm-server`'s backend migrations.

Identical text (`The Qwen3-27b model served via \`llama.cpp\` on the DGX
Spark at \`http://192.168.68.104/v1\` (external DNS: \`https://dgx.blosshomelab.com\`)...`)
appears at:

| File | Line |
|---|---|
| `omneval/CONTEXT.md` | 196 |
| `omneval-pr286/CONTEXT.md` | 196 |
| `omneval-pr307/CONTEXT.md` | 196 |
| `omneval/.claude/worktrees/agent-a06dd556031b944fc/CONTEXT.md` | 192 |
| `omneval/.claude/worktrees/agent-a15ae1f366b5d1697/CONTEXT.md` | 192 |
| `omneval/.claude/worktrees/agent-a1859e51c1d05edd5/CONTEXT.md` | 192 |
| `omneval/.claude/worktrees/agent-a30605265d281a092/CONTEXT.md` | 192 |
| `omneval/.claude/worktrees/agent-a43631c4cbafbdc46/CONTEXT.md` | 192 |
| `omneval/.claude/worktrees/agent-a46015e339f05376f/CONTEXT.md` | 192 |
| `omneval/.claude/worktrees/agent-a48079b336f73f3d2/CONTEXT.md` | 192 |
| `omneval/.claude/worktrees/agent-a4ad32ab1ee05030b/CONTEXT.md` | 192 |
| `omneval/.claude/worktrees/agent-a533d332a0f072ea3/CONTEXT.md` | 192 |
| `omneval/.claude/worktrees/agent-a5706694d653343f4/CONTEXT.md` | 192 |
| `omneval/.claude/worktrees/agent-a79a92b95350a0fa2/CONTEXT.md` | 192 |
| `omneval/.claude/worktrees/agent-a9f6385f8330f3865/CONTEXT.md` | 192 |
| `omneval/.claude/worktrees/agent-abe0f17fabc4fe617/CONTEXT.md` | 192 |
| `omneval/.claude/worktrees/agent-af20af3ce58aabd37/CONTEXT.md` | 192 |
| `omneval/.claude/worktrees/integration-134-145/CONTEXT.md` | 192 |

That's 1 canonical copy in `omneval/CONTEXT.md`, 1 each in `omneval-pr286` and
`omneval-pr307` (presumably PR review checkouts of the same repo), and 15
worktree copies under `omneval/.claude/worktrees/*/CONTEXT.md` (14
agent-prefixed worktrees plus one `integration-134-145` worktree) — all
carrying the same stale text forked at different points. If/when this text
is corrected, it likely only needs fixing at the source (`omneval`'s
canonical `CONTEXT.md` on its default branch); the worktree copies are
ephemeral agent working copies, not independently-maintained config.

### 3.4 `omneval-prwork`

Searched — zero hits.

### 3.5 All other repos — zero hits

`TRELLIS.2`, `arc-raiders`, `candle`, `crypto-tax-tracker`,
`fantasy-football-bot`, `finances`, `fire-calculator`, `golf-sim`,
`greenhouse-interview`, `learn-go`, `local-agent-company`, `local-llm`,
`minto`, `new-claude-skills`, `paperclip`, `the-guide`, `transformers`,
`website`, `would-you-believe-i-am-learning-rust-again`,
`zachbloss-website`, and the loose files directly under `Projects/`
(`local-claude.bat`, `prompt.txt`, `skills-to-create-prd.md`) — none
reference `qwen3.8-27b`, `unsloth/Qwen3.8-27B-NVFP4`, or
`dgx.blosshomelab.com` in any form.

Note: `local-llm` was searched despite the tempting name — it does not
reference the DGX Spark stack at all (zero hits).

---

## 4. Summary — files that would need touching for a full migration

Excluding usage-telemetry-only and already-stale/out-of-sync documentation
(which needs separate cleanup, not a migration edit), the following are the
**live-config** hits that gate consumers actually reaching the DGX Spark
endpoint or naming the old model:

**`home-server`** (5 files, 8 lines): `kubernetes/apps/any-given-sundai/helmrelease.yaml`,
`kubernetes/apps/hermes/helmrelease.yaml`, `kubernetes/apps/hermes/hermes-config-configmap.yaml`,
`kubernetes/apps/documents/paperless-gpt/helmrelease.yaml`,
`kubernetes/apps/agents/devloop/helmrelease.yaml`. Routing manifests
(`kubernetes/apps/ml/dgx-llama-cpp/httproute.yaml`,
`servicemonitor.yaml` comment) reference the endpoint/checkpoint but don't
need edits to keep routing working (hostname/port are model-agnostic); the
`servicemonitor.yaml` comment is just explanatory text.

**Local machine**: `~/.qwen/settings.json` (Qwen Code CLI provider config).

**Other repos**: `any-given-sundai/.env`, `any-given-sundai/.repowise/.env`
(endpoint only, no model-name env var to change); `devloop/agents/.env`
(endpoint only).

**Documentation that's already stale and out of sync** (found along the way,
not itself a migration blocker, but worth flagging to the operator):
`home-server/CONTEXT.md` and `home-server/docs/adr/0001-hermes-agent-deployment.md`
still describe the endpoint as serving `nvidia/Qwen3.6-35B-A3B-NVFP4`
(two model generations behind `qwen3.8-27b`); `omneval`'s `CONTEXT.md` (and
its PR/worktree copies) describes the endpoint as serving `Qwen3-27b` via
`llama.cpp` (one generation behind and wrong backend even for the
`qwen3.8-27b`/vLLM era).

No `~/.config/pi/models.json` was found to update — it doesn't exist on this
machine despite being documented as an example in `dgx-llm-server`'s README.
