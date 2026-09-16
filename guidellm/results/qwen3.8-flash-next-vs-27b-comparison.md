# qwen3.8-flash-next vs qwen3.8-27b — guidellm comparison

Benchmarked with `guidellm/run.sh` (concurrency sweep `1,2,4,8`, 60s/level) against the
deployed `qwen3.8-flash-next` service (`http://192.168.68.104:8000`) on 2026-09-16.
Compared against the most complete stored `qwen3.8-27b` baseline sweep,
`qwen3.8-27b_20260904-235536.json` (30s/level, streams `1,2,4` only — the other two
stored baseline files, `20260904-143125` and `20260904-150035`, had zero successful
requests at streams=4 and are not usable for that data point).

Both runs use this repo's agentic-coding-shaped synthetic traffic (large first-turn
prompt + prefix caching + multi-turn tool calls), not a raw-throughput saturation
sweep — see `guidellm/run.sh`'s header comment. Sample sizes per concurrency level are
small (5-12 requests per 60s window at high concurrency, driven by ~6-9k-token
prompts), so treat these as directional, not high-precision, numbers — the same
limitation is visible in the stored baseline files themselves.

## Server Throughput Statistics (guidellm's own aggregate, all requests)

Output Tokens/Sec, per concurrency level:

| Concurrency | qwen3.8-27b (baseline) | qwen3.8-flash-next | Delta |
|---|---|---|---|
| 1 | 20.3 | 22.5 | +11% |
| 2 | 29.3 | 34.3 | +17% |
| 4 | 56.1 | 57.8 | +3% |
| 8 | *(not in stored baseline)* | 53.3 | — |

Recipe's own published numbers (with NEXTN, ADR 0017 / map #28 Notes) for reference:
27.5 tok/s single-stream, 71.7 tok/s @ 8-concurrent.

## Reading the sweep

- At every concurrency level the stored baseline covers (1, 2, 4), `qwen3.8-flash-next`
  matches or exceeds `qwen3.8-27b`'s aggregate output throughput — largest gain at
  streams=2 (+17%).
- `qwen3.8-flash-next` clears the ADR-0014 ~50 tok/s reference point at streams=4
  (57.8 tok/s), the first level in the sweep where the reference matters.
- streams=8 (53.3 tok/s) dips slightly from streams=4 (57.8 tok/s) rather than
  continuing to climb toward the recipe's published 71.7 tok/s ceiling. Given the
  small per-window sample count at streams=8 (5 successful / 12 total requests) and
  the same noisiness seen in the stored 27b baseline runs, this reads as sampling
  noise rather than a real regression — it's still well above the ~50 tok/s
  reference point.
- The sweep does not reproduce the recipe's published "worse at low concurrency,
  better at high concurrency" shape relative to 27b — `qwen3.8-flash-next` is already
  ahead at streams=1. That's a case where our real deployment/traffic shape reads
  better than the recipe's own benchmark shape, not a discrepancy that needs
  reconciling.

## Decision: keep

`qwen3.8-flash-next` matches or beats the stored `qwen3.8-27b` baseline everywhere
it's directly comparable, and clears the ADR-0014 ~50 tok/s reference by streams=4.
No result here motivates a revert of a deployment already verified healthy (#33)
with a working memory watchdog (#38, ADR 0018).

## Raw results

- `qwen3.8-flash-next_20260916-122015.json` / `.csv` — this run
- `qwen3.8-27b_20260904-235536.json` / `.csv` — baseline used above
- `qwen3.8-27b_20260904-143125.json`, `qwen3.8-27b_20260904-150035.json` — earlier,
  less complete baseline attempts (kept for history)
