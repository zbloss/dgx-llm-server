# ADR 0009: Massive-context cancel/retry storm on restart

**Status:** Documented — no config change made, root cause sits outside this repo
**Date:** 2026-08-15

## Context

While trying to re-measure decode speed after ADR 0008's `spec-draft-n-max`/`p-min` change, a `docker compose logs` capture taken a few minutes after a fresh `llama-server` restart showed something unrelated to spec-decoding: within ~12 minutes of the restart, 3 separate requests carrying **130k-164k-token prompts** (essentially a full agent conversation history resent each time) got their connection torn down (`http client error: Connection handling canceled` / `Failed to read connection`) before generation ever produced useful output — in two cases, cancellation happened within a second of prefill finishing (`progress = 1.00`), with **zero tokens generated**.

Because these requests overlap the same growing conversation, and the prompt-cache's checkpoint chain only matched a small prefix each retry (e.g. an 18,670-token match out of a 163,509-token prompt), most of the checkpoint chain got invalidated and discarded on each attempt (`erased invalidated context checkpoint` x13 in one instance). Each retry re-paid nearly the full prefill cost — 100-350+ seconds of GPU time — for zero forward progress.

Separately, while one slot prefilled a large prompt, the *other* slot's already-in-flight decode was observed crawling at **0.55-0.59 tokens/second** (task 80, `n_decoded = 127→139` over many minutes) — a large, unrelated contention effect purely from sharing 2 parallel slots on the same GPU during a heavy prefill. This contaminated the ADR 0008 re-test that was running at the same time (see that ADR's Consequences).

The user's working theory, confirmed by re-testing after clearing the coding session's context and restarting fresh: **the restart itself triggered this storm.** Multiple coding-agent sessions with pre-existing massive contexts (130k+ tokens each) all reconnected to the freshly-restarted server at once, each resending its full history, each taking minutes to prefill, each getting canceled and retried by its client before finishing — a pile-up that only existed because of the restart, not a steady-state problem. After clearing context and starting from a fresh, short prompt, decode speed measured a clean 21 tokens/second with no cancellations.

## Decision

No config change. This is a restart-transient condition, not a steady-state misconfiguration — `--models-max 2` and `parallel = 2` are not implicated (no eviction or truncation was observed in this capture, only cancel/retry). Documenting it here because it re-explains why ADR 0008's first measurement looked like a regression, and because the two ingredients are still worth being aware of:

1. **Contexts reaching 130k-164k tokens per turn** are the real reason prefill can take minutes on this hardware — independent of any llama.cpp tuning, this is a property of how much history the coding agent(s) are resending per turn.
2. **Concurrent large prefill on one slot measurably starves decode on the other slot** (observed: 0.55-0.59 tokens/second) — a real cost of `parallel = 2` sharing one GPU, worth knowing about even though ADR 0007 correctly chose it over truncating context.

## Alternatives considered

**Tune a server-side `--timeout` flag to stop the cancellation.** Not pursued — the disconnect is a client/socket-level event (`Connection handling canceled`), not an explicit llama.cpp timeout firing (`compose.yaml` doesn't set `--timeout` on `llama-server` at all). The actual timeout, if one is involved, lives in whatever HTTP client the coding agent uses, or in Traefik's proxy config for `dgx.blosshomelab.com` (in the `home-server` repo, not this one) — out of scope here without more evidence of which one is cutting the connection.

**Investigate/tune either of those timeouts now.** Deferred — the user's own restart-and-clear-context test already resolved the symptom for the current session, and this only reproduces on a cold restart with multiple pre-existing massive-context sessions reconnecting simultaneously. Worth a closer look if it recurs on a future restart with agents already mid-session.

## Consequences

- ADR 0008's `spec-draft-n-max=4`/`spec-draft-p-min=0.5` re-test needs to happen under conditions like this capture's post-clear state (single request, fresh/short context, no concurrent large prefill) to produce a trustworthy number — see that ADR.
- If this recurs on a future restart, the fix is more likely "prevent multiple massive-context sessions from all reconnecting and re-prefilling at once" (client/proxy-side) or "keep agent context sizes smaller" (agent-side) than anything tunable in this repo's `config.ini` or `compose.yaml`.
