#!/usr/bin/env python3
"""
Benchmark vLLM throughput and latency.
Measures TTFT and tokens/second using streaming for two workloads:
  - short: quick factual prompt (minimal thinking)
  - long: multi-step reasoning prompt (heavy thinking)

Usage:
  python scripts/benchmark.py [--base-url http://localhost:8000] [--runs 3]
"""
import argparse
import json
import statistics
import time
import urllib.request
from dataclasses import dataclass, field


BASE_URL = "http://localhost:8000"
MODEL = "qwen3.8-27b"

PROMPTS = {
    "no_think": {
        "messages": [{"role": "user", "content": "What is the capital of France? /no_think"}],
        "max_tokens": 32,
    },
    "medium_effort": {
        "messages": [
            {
                "role": "user",
                "content": (
                    "Write a Python function that finds all prime numbers up to N "
                    "using the Sieve of Eratosthenes. Include type hints and a brief docstring."
                ),
            }
        ],
        "max_tokens": 512,
        "chat_template_kwargs": {"reasoning_effort": "medium"},
    },
    "xhigh_effort": {
        "messages": [
            {
                "role": "user",
                "content": (
                    "Design a distributed rate-limiter service in Python. "
                    "Explain the architecture, data structures, and concurrency model. "
                    "Then implement the core TokenBucket class with Redis-backed persistence."
                ),
            }
        ],
        "max_tokens": 1024,
        "chat_template_kwargs": {"reasoning_effort": "xhigh"},
    },
}


@dataclass
class Result:
    prompt_name: str
    ttft_s: float        # time to first token (any token, including thinking)
    ttfat_s: float       # time to first answer token (post-thinking)
    total_s: float
    completion_tokens: int
    thinking_tokens: int
    answer_tokens: int
    tps: float           # answer_tokens / (total_s - ttfat_s), clipped


def stream_completion(base_url: str, payload: dict) -> Result:
    url = f"{base_url}/v1/chat/completions"
    data = {k: v for k, v in payload.items() if not k.startswith("_")}
    body = json.dumps({
        "model": MODEL,
        "stream": True,
        "stream_options": {"include_usage": True},
        **data,
    }).encode()

    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    # Note: chat_template_kwargs is passed as a top-level vLLM extension field, not in extra_body

    t_start = time.perf_counter()
    ttft: float | None = None
    ttfat: float | None = None   # time to first answer (content) token
    thinking_chunks = 0
    answer_chunks = 0
    usage_data = None

    with urllib.request.urlopen(req) as resp:
        for raw_line in resp:
            line = raw_line.decode().strip()
            if not line.startswith("data: "):
                continue
            data_str = line[6:]
            if data_str == "[DONE]":
                break
            try:
                chunk = json.loads(data_str)
            except json.JSONDecodeError:
                continue

            if chunk.get("usage"):
                usage_data = chunk["usage"]
                continue

            choices = chunk.get("choices", [])
            if not choices:
                continue

            delta = choices[0].get("delta", {})
            now = time.perf_counter()

            if delta.get("reasoning"):  # vLLM emits thinking via delta.reasoning
                if ttft is None:
                    ttft = now - t_start
                thinking_chunks += 1

            if delta.get("content"):
                if ttft is None:
                    ttft = now - t_start
                if ttfat is None:
                    ttfat = now - t_start
                answer_chunks += 1

    t_end = time.perf_counter()
    total_s = t_end - t_start

    completion_tokens = 0
    if usage_data:
        completion_tokens = usage_data.get("completion_tokens", 0)

    ttft = ttft or total_s
    ttfat = ttfat or total_s

    # Split thinking vs answer tokens. vLLM streams thinking via delta.reasoning and
    # answer via delta.content. The usage_data.completion_tokens covers both.
    # Use chunk counts as a proxy (each chunk ≈ 1 or more tokens; imprecise but directional).
    if thinking_chunks > 0 and completion_tokens > 0:
        # Estimate thinking tokens from chunk ratio
        total_chunks = thinking_chunks + answer_chunks
        if total_chunks > 0:
            thinking_tokens = round(completion_tokens * thinking_chunks / total_chunks)
        else:
            thinking_tokens = 0
        answer_tokens = completion_tokens - thinking_tokens
    else:
        thinking_tokens = 0
        answer_tokens = completion_tokens or answer_chunks or 1

    # tok/s: answer tokens over time spent generating the answer portion
    answer_gen_time = max(total_s - ttfat, 0.01)
    tps = max(answer_tokens, 1) / answer_gen_time

    return Result(
        prompt_name=payload.get("_name", "?"),
        ttft_s=ttft,
        ttfat_s=ttfat,
        total_s=total_s,
        completion_tokens=completion_tokens,
        thinking_tokens=thinking_tokens,
        answer_tokens=answer_tokens,
        tps=tps,
    )


def run_benchmark(base_url: str, runs: int):
    print(f"\nvLLM Benchmark  |  {base_url}  |  model={MODEL}  |  runs={runs}\n")
    print(f"{'Prompt':<16} {'TTFT(s)':>8} {'TTFAT(s)':>9} {'Total(s)':>9} {'CmpTok':>7} {'ThinkTok':>9} {'AnsTok':>7} {'ans tok/s':>10}")
    print("-" * 80)

    all_results: dict[str, list[Result]] = {}

    for name, payload in PROMPTS.items():
        results: list[Result] = []
        payload = {**payload, "_name": name}
        for i in range(runs):
            print(f"  {name} run {i+1}/{runs}...", end="\r", flush=True)
            try:
                r = stream_completion(base_url, payload)
                results.append(r)
            except Exception as e:
                print(f"\n  ERROR on {name} run {i+1}: {e}")

        all_results[name] = results
        if results:
            avg_ttft  = statistics.mean(r.ttft_s for r in results)
            avg_ttfat = statistics.mean(r.ttfat_s for r in results)
            avg_total = statistics.mean(r.total_s for r in results)
            avg_cmp   = statistics.mean(r.completion_tokens for r in results)
            avg_think = statistics.mean(r.thinking_tokens for r in results)
            avg_ans   = statistics.mean(r.answer_tokens for r in results)
            avg_tps   = statistics.mean(r.tps for r in results)
            print(f"  {name:<16} {avg_ttft:>8.2f} {avg_ttfat:>9.2f} {avg_total:>9.2f} {avg_cmp:>7.0f} {avg_think:>9.0f} {avg_ans:>7.0f} {avg_tps:>10.1f}")

    print()
    overall_tps = [r.tps for rs in all_results.values() for r in rs]
    if overall_tps:
        print(f"Overall avg ans tok/s: {statistics.mean(overall_tps):.1f}  |  median: {statistics.median(overall_tps):.1f}")
    print()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=BASE_URL)
    parser.add_argument("--runs", type=int, default=3)
    args = parser.parse_args()
    run_benchmark(args.base_url, args.runs)


if __name__ == "__main__":
    main()
