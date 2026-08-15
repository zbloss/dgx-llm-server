# ADR 0008: `spec-draft-p-min` and a higher `spec-draft-n-max`

**Status:** Re-applied, pending a clean re-measurement — see Consequences
**Date:** 2026-08-15

## Context

A third party reported running `Qwen3.8-27B` with `--spec-type draft-mtp` plus `--spec-draft-p-min 0.8` and a much higher `--spec-draft-n-max` (their command used `--spec-default`, which fills in numeric spec-decoding defaults; a separate GitHub discussion tuning MTP found `spec-draft-n-max=16` + `spec-draft-p-min=0.8` gave ~15-20% real throughput gains on code-generation workloads). Our `config.ini` was already running `spec-type = draft-mtp` on both profiles (`spec-draft-n-max = 2`) but had never set `spec-draft-p-min` at all, leaving it at llama.cpp's default of `0.00`.

`spec-draft-p-min` tells the MTP draft head to stop drafting further tokens once its own confidence drops below the threshold, instead of continuing to guess and having the base model reject the low-confidence tokens anyway. Unlike the `parallel` tuning in ADR 0007, this is safe to experiment with: speculative decoding output is always exactly verified against the full model's actual distribution regardless of draft confidence, so a wrong value only costs speed (wasted draft compute, or leaving gains on the table) — it can never change what the model actually outputs.

## Decision

Set `spec-draft-n-max = 4` (up from 2) and `spec-draft-p-min = 0.5` on both `qwen3.6-35b-a3b` and `qwen3.8-27b`. Deliberately more conservative than the third party's reported `n-max=16` / `p-min=0.8` — those numbers were tuned on different hardware (a single-GPU 3090) and a different model, and adopting them wholesale without verification would repeat the exact mistake ADR 0007 already made and reverted with `parallel=4`: trusting a number that worked somewhere else without checking it against this stack's own behavior.

`--spec-default` itself (the bundling flag from the third party's command) was not adopted — `config.ini` already sets `spec-type` explicitly per profile, and setting the numeric spec-decoding flags explicitly here keeps them visible and documented in the one place this repo already keeps that reasoning, rather than behind an opaque preset.

## Consequences

**First measurement (2026-08-15, same day) looked like a regression, but was confounded — see ADR 0009.**

Baselines (both established before this change, at `spec-draft-n-max=2`, no `p-min`):
- `qwen3.6-35b-a3b`: ~47-48 tokens/second (from the `logs.txt` that informed ADR 0007)
- `qwen3.8-27b`: 25-40 tokens/second (per user)

After first deploying `spec-draft-n-max=4` / `spec-draft-p-min=0.5` on both profiles, a `docker compose logs` capture showed `qwen3.6-35b-a3b` at 30.94 tokens/second (one sample) and `qwen3.8-27b` at ~4-17 tokens/second (median ~7) across ~46 requests — both well below baseline, so the change was reverted back to `spec-draft-n-max=2`/no `p-min`.

That capture turned out to be unusable as a signal: ADR 0009 found the server was, at the same time, fielding a repeated cancel/retry storm of 130k-164k-token requests, and directly observed a *concurrent, unrelated* generation crawling at 0.55-0.59 tokens/second purely because another slot was mid-prefill on a huge prompt — a contention effect large enough to swamp any real difference `spec-draft-n-max`/`p-min` could make. The regression was real in that capture, but there's no way to attribute it to this setting versus the contention storm.

**Re-applied** `spec-draft-n-max = 4` / `spec-draft-p-min = 0.5` on both profiles once more, this time to be re-measured under clean conditions: a single request, fresh/short context, no concurrent large prefill on the other slot — the same conditions that produced the ~21 tokens/second `qwen3.8-27b` reading at `n-max=2` right after the ADR 0009 restart. Compare a post-deploy clean sample against that number (and against the ~25-40 tokens/second longer-run baseline) before drawing any conclusion this time.

No correctness/output-quality risk either way — see Context.
