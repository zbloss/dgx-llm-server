# ADR 0008: `spec-draft-p-min` and a higher `spec-draft-n-max`

**Status:** Accepted (values unverified on this hardware — see Consequences)
**Date:** 2026-08-15

## Context

A third party reported running `Qwen3.8-27B` with `--spec-type draft-mtp` plus `--spec-draft-p-min 0.8` and a much higher `--spec-draft-n-max` (their command used `--spec-default`, which fills in numeric spec-decoding defaults; a separate GitHub discussion tuning MTP found `spec-draft-n-max=16` + `spec-draft-p-min=0.8` gave ~15-20% real throughput gains on code-generation workloads). Our `config.ini` was already running `spec-type = draft-mtp` on both profiles (`spec-draft-n-max = 2`) but had never set `spec-draft-p-min` at all, leaving it at llama.cpp's default of `0.00`.

`spec-draft-p-min` tells the MTP draft head to stop drafting further tokens once its own confidence drops below the threshold, instead of continuing to guess and having the base model reject the low-confidence tokens anyway. Unlike the `parallel` tuning in ADR 0007, this is safe to experiment with: speculative decoding output is always exactly verified against the full model's actual distribution regardless of draft confidence, so a wrong value only costs speed (wasted draft compute, or leaving gains on the table) — it can never change what the model actually outputs.

## Decision

Set `spec-draft-n-max = 4` (up from 2) and `spec-draft-p-min = 0.5` on both `qwen3.6-35b-a3b` and `qwen3.8-27b`. Deliberately more conservative than the third party's reported `n-max=16` / `p-min=0.8` — those numbers were tuned on different hardware (a single-GPU 3090) and a different model, and adopting them wholesale without verification would repeat the exact mistake ADR 0007 already made and reverted with `parallel=4`: trusting a number that worked somewhere else without checking it against this stack's own behavior.

`--spec-default` itself (the bundling flag from the third party's command) was not adopted — `config.ini` already sets `spec-type` explicitly per profile, and setting the numeric spec-decoding flags explicitly here keeps them visible and documented in the one place this repo already keeps that reasoning, rather than behind an opaque preset.

## Consequences

- **Not yet empirically verified on this hardware+model combo.** The captured `logs.txt` that informed ADR 0007 gives a decode-speed baseline at `spec-draft-n-max=2` — roughly 47-48 tokens/second on `qwen3.6-35b-a3b` (`docker compose logs llama-server | grep "eval time"`, the ` eval time = ... tokens per second` lines, not the `prompt eval time` ones). After deploying this change, re-check the same grep and compare. If it doesn't measurably improve, or regresses, revert to `spec-draft-n-max = 2` and drop `spec-draft-p-min` rather than pushing toward the more aggressive third-party values on faith.
- If this baseline check does confirm a real gain, the third party's more aggressive `n-max=16`/`p-min=0.8` becomes a reasonable next experiment — but only as a follow-up measured step, not adopted here directly.
- No correctness/output-quality risk either way — see Context.
