# ADR 0008: `spec-draft-p-min` and a higher `spec-draft-n-max`

**Status:** Reverted — measured regression, see Consequences
**Date:** 2026-08-15

## Context

A third party reported running `Qwen3.8-27B` with `--spec-type draft-mtp` plus `--spec-draft-p-min 0.8` and a much higher `--spec-draft-n-max` (their command used `--spec-default`, which fills in numeric spec-decoding defaults; a separate GitHub discussion tuning MTP found `spec-draft-n-max=16` + `spec-draft-p-min=0.8` gave ~15-20% real throughput gains on code-generation workloads). Our `config.ini` was already running `spec-type = draft-mtp` on both profiles (`spec-draft-n-max = 2`) but had never set `spec-draft-p-min` at all, leaving it at llama.cpp's default of `0.00`.

`spec-draft-p-min` tells the MTP draft head to stop drafting further tokens once its own confidence drops below the threshold, instead of continuing to guess and having the base model reject the low-confidence tokens anyway. Unlike the `parallel` tuning in ADR 0007, this is safe to experiment with: speculative decoding output is always exactly verified against the full model's actual distribution regardless of draft confidence, so a wrong value only costs speed (wasted draft compute, or leaving gains on the table) — it can never change what the model actually outputs.

## Decision

Set `spec-draft-n-max = 4` (up from 2) and `spec-draft-p-min = 0.5` on both `qwen3.6-35b-a3b` and `qwen3.8-27b`. Deliberately more conservative than the third party's reported `n-max=16` / `p-min=0.8` — those numbers were tuned on different hardware (a single-GPU 3090) and a different model, and adopting them wholesale without verification would repeat the exact mistake ADR 0007 already made and reverted with `parallel=4`: trusting a number that worked somewhere else without checking it against this stack's own behavior.

`--spec-default` itself (the bundling flag from the third party's command) was not adopted — `config.ini` already sets `spec-type` explicitly per profile, and setting the numeric spec-decoding flags explicitly here keeps them visible and documented in the one place this repo already keeps that reasoning, rather than behind an opaque preset.

## Consequences

**Measured after deploy — this was a regression, not a gain.**

Baselines (both established before this change, at `spec-draft-n-max=2`, no `p-min`):
- `qwen3.6-35b-a3b`: ~47-48 tokens/second (from the `logs.txt` that informed ADR 0007)
- `qwen3.8-27b`: 25-40 tokens/second (per user)

After deploying `spec-draft-n-max=4` / `spec-draft-p-min=0.5` on both profiles, a fresh `docker compose logs` capture (`grep "eval time"`, the ` eval time = ... tokens per second` lines) showed:
- `qwen3.6-35b-a3b`: 30.94 tokens/second (single sample, taken right after a cold load — not a large sample, but the only data point available and it points the same direction as qwen3.8-27b below)
- `qwen3.8-27b`: ~4-17 tokens/second, median ~7, across ~46 completed requests over a 67-hour window with no improving trend over time

Draft acceptance rates were healthy throughout (mostly 0.5-0.95), so the MTP draft head itself wasn't misbehaving — whatever ate the gain sits elsewhere (possibly slot contention or draft-verification overhead outweighing the benefit of drafting 4 tokens ahead instead of 2 at this p-min). Root cause not investigated further; not worth chasing given the direction of the result.

**Reverted:** both profiles back to `spec-draft-n-max = 2`, `spec-draft-p-min` removed (back to llama.cpp's default `0.00`). The third party's more aggressive `n-max=16`/`p-min=0.8` is not worth trying as a follow-up — the more conservative version of the same lever already regressed, so there's no reason to expect the more aggressive one would do better on this hardware+model combo.

No correctness/output-quality risk either way — see Context.
