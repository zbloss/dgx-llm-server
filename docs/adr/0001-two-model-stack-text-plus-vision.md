# ADR 0001: Two-model stack — Text Model + Vision Model

**Status:** Superseded by ADR 0002  
**Date:** 2026-05-26

## Context

The DGX Spark has 128 GB of unified memory. The original single-model setup (Nemotron-3-Super at `--gpu-memory-utilization 0.90`) consumed ~115 GB, leaving no room for a second model.

The agent pipeline requires vision capability for the QA Agent phase: browser screenshot analysis, frontend verification, and E2E tracing. Nemotron-3-Super is text-only and cannot accept image inputs.

## Decision

Run two simultaneous vLLM instances with memory-reduced settings:

**Text Model** — `nvidia/nemotron-3-super` (nvidia/NVIDIA-Nemotron-3-Super-120B-A12B-NVFP4)  
`--gpu-memory-utilization 0.55` (70.4 GB cap)  
`--max-model-len 131072` (reduced from 262144)  
`--max-num-seqs 4` (reduced from 8)  
`--mamba_ssm_cache_dtype float16` (corrected from float32)

**Vision Model** — `qwen3-vl-32b` (RedHatAI/Qwen3-VL-32B-Instruct-NVFP4)  
`--gpu-memory-utilization 0.30` (38.4 GB cap)  
`--max-model-len 65536`  
`--max-num-seqs 4`

Combined allocation: ~108.8 GB of 128 GB (85%).

## Alternatives considered

**Single text model (Nemotron only):** No vision support. The QA Agent cannot analyze screenshots, which is a hard requirement for frontend testing.

**Replace Nemotron with a single vision model:** No vision model in the same parameter class matches Nemotron's agentic reasoning, 1M-token context, and LiveCodeBench score (78.44%). Qwen3-VL-235B MoE (vision + strong reasoning) would require ~117 GB for weights alone — does not fit alongside anything else.

**Gemma-4-31B as the vision model:** Near-perfect benchmark retention but ~52% SWE-bench Verified vs. Qwen3-VL family's stronger coding. The QA Agent's API testing and E2E tracing workloads are fundamentally coding tasks.

**Gemma-4-26B-A4B MoE as the vision model:** Only 15.7 GB loaded (very memory-efficient) and DGX Spark verified, but only 3.8B active parameters — insufficient for complex QA reasoning even with Nemotron as advisor fallback. Also requires a custom vLLM patch file.

## Consequences

- The context window for Nemotron is capped at 128K. Agent sessions that accumulate more than ~128K tokens must compact before the next call. This matches the existing operator practice of compacting at ~130K tokens.
- The QA Agent owns its full session on the Vision Model, including backend API testing and E2E tracing. It escalates to Nemotron only for isolated reasoning-heavy sub-tasks, not for session ownership.
- The compose stack now has two vLLM service entries. The two containers may use different `vllm/vllm-openai` image tags, since Nemotron is locked to `v0.20.0` by NVIDIA's DGX Spark guide and Qwen3-VL may require a newer release.
- LiteLLM routes by Canonical Model Name: clients send `nvidia/nemotron-3-super` or `qwen3-vl-32b` in the `model` field.
