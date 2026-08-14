# ADR 0006: Patch chat templates to tolerate mid-conversation system messages

**Status:** Accepted
**Date:** 2026-08-14
**Amends:** ADR 0005 (same model stack, no model/routing change)

## Context

Claude Code, connected directly via `ANTHROPIC_BASE_URL=https://dgx.blosshomelab.com` (`local-claude.bat`), fails on both router profiles with:

```
API Error: 400 Unable to generate parser for this template. Automatic parser generation failed:
  ...
  Error: Jinja Exception: System message must be at the beginning.
```

Both `unsloth/Qwen3.6-35B-A3B-GGUF` and `unsloth/Qwen3.8-27B-GGUF` embed the same upstream Qwen chat template logic:

```jinja
{%- if message.role == "system" %}
    {%- if not loop.first %}
        {{- raise_exception('System message must be at the beginning.') }}
    {%- endif %}
{%- endif %}
```

Any `system`-role message that isn't `messages[0]` trips this. The first turn of a Claude Code session is fine (one leading system message); later turns fail once Claude Code injects an additional `system`-role entry mid-conversation (e.g. system-reminder content) - a pattern other OpenAI-SDK clients on this box (paperless-gpt, hermes, devloop) don't hit because they only ever send a single leading system message.

This is not an llama.cpp bug: `--jinja` is correctly executing the template's own validation. The exact same error against the exact same model family is filed upstream at [ggml-org/llama.cpp#20733](https://github.com/ggml-org/llama.cpp/issues/20733), closed **not planned** - llama.cpp considers this the template author's rule to enforce, not theirs to relax. Other agentic coding CLIs hit the identical failure against llama.cpp for the same reason ([obra/superpowers#742](https://github.com/obra/superpowers/issues/742)).

## Decision

Stop relying on the GGUF-embedded chat template. Add `models/chat-templates/<profile>.jinja` - a byte-for-byte copy of each model's upstream template with one change: a non-first `system`-role message renders as a `user`-role turn (prefixed `[System reminder]`) instead of calling `raise_exception`. Wire it in per-profile via `chat-template-file = /chat-templates/<profile>.jinja` in `models/config.ini`, mounted read-only into the container (`./models/chat-templates:/chat-templates:ro` in `compose.yaml`).

Content is preserved (rendered as a user turn) rather than silently dropped, since the alternative - skipping the message entirely - would hide real content (tool-use reminders, context injections) from the model without any signal that something was omitted.

`.github/workflows/sync-models.yml` now also triggers on `models/chat-templates/**` and copies the directory to the deploy host alongside `compose.yaml` and `models/config.ini`.

## Alternatives considered

**Front the server with a proxy that rewrites non-first system messages before they reach llama-server.** Handles the same failure mode for any future model without a template diff each time. Rejected for now - it's another long-running process to deploy, monitor, and keep available, for a fix that a static file already covers for the two models actually in use. Revisit if a third client with a different message-shaping pattern shows up, or if a future model's template can't be patched this cleanly.

**Wait for upstream llama.cpp to relax this.** Rejected - the issue is closed not-planned; there's no upstream fix to wait for.

**Get Claude Code to stop injecting mid-conversation system messages.** Not this repo's call to make - Claude Code's system-reminder mechanism isn't configurable from the client side ADR 0005's `local-claude.bat` controls, and other future agentic clients will very likely do the same thing.

## Consequences

- Each `[profile]` section in `models/config.ini` now needs its own `chat-template-file` pointing at a matching `.jinja` copy. Adding a new model profile means adding a template file too (start from the model's upstream `chat_template.jinja` and apply the same patch), not just a `config.ini` section.
- The patched templates are hand-maintained forks - if a model card ships a template update (e.g. a Qwen point release), the local copy won't pick it up automatically and needs re-diffing against upstream.
- No change to routing, sizing, or the `--models-max 1` swap behavior from ADR 0005 - this only changes how each profile renders messages, not which model is resident or when.
