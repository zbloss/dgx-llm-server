# Research: Qwen3.8-Flash-Next checkpoint — RadixArk vs NVIDIA NVFP4

**Date:** 2026-09-16
**Ticket:** [zbloss/dgx-llm-server#30](https://github.com/zbloss/dgx-llm-server/issues/30) (part of map [#28](https://github.com/zbloss/dgx-llm-server/issues/28))
**Question:** Which checkpoint should the DGX Spark / SGLang deploy of Qwen3.8-Flash-Next standardize on — `nvidia/Qwen3.8-Flash-Next-NVFP4` (official) or `RadixArk/Qwen3.8-Flash-Next-NVFP4` (community, `sglang`-tagged)?

## Recommendation

**Use `RadixArk/Qwen3.8-Flash-Next-NVFP4`.** It is the only one of the two whose on-disk file layout actually matches SGLang's NVMe-PLE-offload recipe, its README explicitly targets SGLang, and its publisher has a credible, pre-existing track record — including being cited by SGLang's own in-tree docs for a sibling model. It should not be treated as a fully de-risked choice: see [Caveats](#caveats--could-not-verify) below before this becomes the sole load-bearing weight source for the deploy.

## 1. File listing / size comparison

Source: live Hub file listings, `https://huggingface.co/nvidia/Qwen3.8-Flash-Next-NVFP4/tree/main` and `https://huggingface.co/RadixArk/Qwen3.8-Flash-Next-NVFP4/tree/main` (fetched via the HF Hub filesystem API, 2026-09-16).

### `nvidia/Qwen3.8-Flash-Next-NVFP4`

- 10 standard HF shards (`model-00001-of-00010.safetensors` … `model-00010-of-00010.safetensors`) + `model.safetensors.index.json`, totaling **78,962,697,648 bytes (≈78.96 GB / 73.5 GiB)**.
- One additional monolithic file, **`model-fp8-mtp-ple.safetensors` = 53,717,551,730 bytes (≈53.72 GB / 50.03 GiB)**, which the [model card](https://huggingface.co/nvidia/Qwen3.8-Flash-Next-NVFP4) confirms bundles *both* the FP8 MTP speculative-decoding weights *and* the FP8 PLE n-gram embedding table together ("The complete MTP module and PLE n-gram embedding match Qwen/Qwen3.8-Flash-Next-FP8 byte-for-byte... FP8 MTP routed-expert tensors and PLE tensors were copied from that checkpoint").
- Repo total: **132,680,249,378 bytes (≈132.7 GB / 123.6 GiB)**.
- Architecture tag: `qwen4_exp` (per Hub metadata).

### `RadixArk/Qwen3.8-Flash-Next-NVFP4`

- 192 expert-shard files (`layer-000NN-experts-XXXX-YYYY.safetensors`, 48 layers × 4 expert ranges) — the routed-expert NVFP4 weights, plus matching `*.complete.json` sidecar metadata files per shard.
- 4 BF16 shard files (`model-bf16-00001`, `-00010`, `-00011`, `-00012` — note the gap: shards 2–9 aren't present, meaning the non-expert/MTP tensors were consolidated into just these four files rather than a full 1–12 run).
- **10 dedicated PLE shard files**, `model-plefp8-00000.safetensors` … `model-plefp8-00009.safetensors`, summing to **51,200,267,901 bytes (≈51.2 GB / 47.68 GiB)** — this is a standalone, purely-PLE file group, distinct from the main/MTP weights.
- Repo total per its own `qualification-notes.md`: **206 weight shards (192 routed + 4 BF16 + 10 PLE-FP8), 296,475 tensors, 135,253,624,416 bytes (≈135.25 GB / 125.96 GiB)**.
- Architecture tag: `qwen4_exp` (same as NVIDIA's — see §3).

### Which one matches the expected split?

The map ticket's working estimate was "~79GB main + ~47.7GiB FP8 PLE table." **RadixArk's dedicated PLE shard group (47.68 GiB) matches that figure almost to the decimal.** NVIDIA's PLE-bearing file is 50.03 GiB, but that number isn't a clean PLE figure at all — it's PLE *plus* MTP bundled together in one blob, so it overstates what SGLang's `--ple-offload-backend file` would actually need to push to NVMe, and understates it doesn't isolate cleanly.

This is the material technical differentiator: SGLang's PLE-offload recipe wants a PLE tensor set it can push to NVMe (tolerant of NVMe latency — it's only touched for n-gram lookups), separate from the MTP weights, which must stay GPU-resident since they're read every decode step for speculation. NVIDIA's checkpoint conflates the two into one file; RadixArk's checkpoint has already been split so PLE is its own artifact and MTP tensors live with the main BF16 weights. **I could not independently confirm from SGLang's own PLE-offload source code (see §2) whether the loader actually requires this file-level separation, or whether it offloads by tensor name regardless of originating shard file** — so treat the "file layout matches the recipe" argument as strong circumstantial evidence, not a proven requirement.

## 2. What the `qwen4-main-squashed` branch / dev image reference

Source: `sgl-project/sglang`, branch `qwen4-main-squashed` @ commit [`4ccff141db`](https://github.com/sgl-project/sglang/commit/4ccff141dbe992794f9da6c3aa23535b4f72000d) (fetched 2026-09-16). The merge PR [sgl-project/sglang#36497](https://github.com/sgl-project/sglang/pull/36497) ("Introduce Qwen 3.8 Flash Next") is confirmed **closed, not merged** (+20,609/−116, never got the `run-ci` label, no reviews recorded before closing).

The branch does contain first-party `qwen4_exp` support:
- `python/sglang/srt/configs/qwen4_exp.py`
- `python/sglang/srt/models/qwen4_exp.py`, `qwen4_exp_mtp.py`, `qwen4_exp_ple_table.py`
- `python/sglang/kernels/ops/qwen4_ple.py`
- PLE-offload unit tests: `test/registered/kernels/ops/embeddings/test_qwen4_ple_offload.py`, `test_qwen4_ple_offload_tp4.py`, plus kernel/fusion/norm tests for the PLE table.

**I searched this code (configs, models, and the PLE-offload tests) for any reference to `nvidia/Qwen3.8-Flash-Next-NVFP4` or `RadixArk/Qwen3.8-Flash-Next-NVFP4` and found none** — the tests use synthetic random tensors, not a real checkpoint id, and the config/model source has no hardcoded checkpoint pointer. So there is no first-party statement, in this branch, of which checkpoint it was built or tested against. This is a real gap: step 2 of this ticket's assignment could not be answered directly.

As a proxy signal: this same branch's cookbook docs (`docs/cookbook/autoregressive/Qwen/Qwen3.8.mdx`) — for the *sibling* Qwen3.8-2.4T-A95B model, not Flash-Next — explicitly recommend `RadixArk/Qwen3.8-2.4T-A95B-NVFP4` as the Blackwell NVFP4 checkpoint and `RadixArk/Qwen3.8-2.4T-A95B-DSpark` as the speculative-decoding draft model. There is no equivalent Flash-Next-specific cookbook page in this branch. So SGLang's own documentation already treats RadixArk as its standard NVFP4 checkpoint publisher for at least one other model in this family — but this is an analogy, not a direct statement about Flash-Next.

## 3. Does the `qwen4_exp` error (sgl-project/sglang#39497) reproduce on the dev image?

Source: [sgl-project/sglang#39497](https://github.com/sgl-project/sglang/issues/39497), opened 2026-09-15, still **open**, one comment, no maintainer response.

- **Original report** (author `Sharadhonavar`): running `nvidia/Qwen3.8-Flash-Next-NVFP4` on `nvcr.io/nvidia/sglang:26.08-py3` (the official NVIDIA registry image, on a GB10 DGX box) fails with `ValueError: The checkpoint you are trying to load has model type qwen4_exp but Transformers does not recognize this architecture`. Manually upgrading `transformers` inside that container to 5.16/5.17 hits a *different* failure (`'qwen3_asr' is already used by a Transformers config, pick another name`), i.e. hand-patching the stock image doesn't cleanly work either.
- **Follow-up comment**, same author: switched checkpoints to `RadixArk/Qwen3.8-Flash-Next-NVFP4` but *also* switched to `lmsysorg/sglang:latest` (still not the dev image) — same `KeyError: 'qwen4_exp'` failure, identical root cause.
- **Neither report used `lmsysorg/sglang:dev-qwen38-next-local` or the `qwen4-main-squashed` branch.** No one in the thread has reported testing either checkpoint on the dev image as of 2026-09-16, and the issue has no maintainer reply or resolution.

**Conclusion on this point:** the error is architecture-registration/image-related, not checkpoint-specific — it reproduces identically regardless of which checkpoint is used, as long as the image's `transformers`/SGLang don't know about `qwen4_exp`. The dev branch's own source (§2) directly implements the missing `qwen4_exp` config/model classes, which is strong circumstantial evidence the error would *not* reproduce on `dev-qwen38-next-local` — but **this is inferred from source presence, not empirically confirmed anywhere I could find.** Flag this as unverified going into the actual deploy ticket.

## 4. Provenance: is RadixArk trustworthy as a maintainer?

Source: HF org profile (`https://huggingface.co/RadixArk`, fetched 2026-09-16) and Hub repo search.

- RadixArk is a Hub **organization**, not a personal/anonymous account: "We are building open source! SGLang: https://www.sglang.io/ Miles: https://github.com/radixark/miles", with a public website (radixark.com), Twitter (@radixark), GitHub org, and LinkedIn company page, and a stated team size of 21 members.
- 16 public model repos on the Hub, several with substantial usage: `RadixArk/Qwen3.8-27B-NVFP4` (2.4M downloads), `RadixArk/Kimi-K3-DSpark` (3.0M downloads), `RadixArk/Qwen3.8-Flash-Next-NVFP4` itself (310K downloads, 115 likes) — this is not a fresh/throwaway account, it has an established quantization/speculative-decoding release history across several model families (Qwen3.8, Kimi-K3, GLM-5.3, Muse-Glimmer).
- Corroborating signal from §2: SGLang's own in-tree docs already point at RadixArk repos for a sibling Qwen3.8 model's NVFP4 and DSpark checkpoints, i.e. this isn't the first time SGLang's documentation has trusted a RadixArk artifact.
- The `Qwen3.8-Flash-Next-NVFP4` repo itself carries unusually granular self-published audit artifacts beyond what NVIDIA's README provides: `qualification-notes.md`, `validate_checkpoint_report.json`, `validate_scales_report.json`, `audit_unchanged_report.json`, `gsm8k_metrics.json`, `aime26_metrics.json`, `smoke_report.json`. Per `audit_unchanged_report.json`: 1,562 non-quantized tensors (118.4 GB) byte-compared identical to source, including all 31 MTP tensors, verdict "pass." Per `qualification-notes.md`: GSM8K 97.27% (in-band vs. a 97.12–97.50% BF16 reference range) and AIME26 98.75% pass@1 (in-band vs. the BF16 reference run).

**Caveats found in the same self-published material** (see next section) temper this — the repo openly labels itself a "private candidate release," and one cited dependency does not resolve.

## Caveats / could-not-verify

- **RadixArk's own README labels this checkpoint "a private candidate release"** (verbatim from `https://huggingface.co/RadixArk/Qwen3.8-Flash-Next-NVFP4`) — RadixArk itself does not present this as a finished, stable artifact.
- **Hardware validation gap for GB10 specifically.** RadixArk's README states "Supported Hardware Microarchitecture Compatibility: NVIDIA Blackwell (validated on GB300 and B300)" — not GB10/DGX Spark. NVIDIA's own checkpoint is explicitly scoped to B200/B300 only, too. **Neither publisher claims GB10 validation** — this deploy would be a first for either checkpoint on this exact hardware, regardless of which is chosen.
- **Unresolved citation.** `qualification-notes.md` names a required PLE loader dependency, "`Qiaolin-Yu/sglang-qwen-next` PR #40" — I could not find this repository on GitHub (`gh repo view Qiaolin-Yu/sglang-qwen-next` fails to resolve). It may be renamed, deleted, or private, or the citation may simply be stale/incorrect. Functionally this is lower-risk than it sounds, since the `qwen4-main-squashed` branch (§2) independently contains its own `qwen4_exp_ple_table.py` PLE-loading code — but the specific external reference in RadixArk's docs is unverified.
- **Quantization tooling provenance.** RadixArk quantized against an unreleased/dev **snapshot** of NVIDIA Model Optimizer pinned to commit `87c9f8cf83021957d1a1a575c90c9a4eaaf7ef0c` (per `conversion_environment.json`), not the public `nvidia-modelopt` v0.46.0 PyPI release that NVIDIA's own checkpoint was built with. Less standard/reproducible tooling, partially offset by RadixArk's detailed self-audit reporting (above).
- **No end-to-end success report exists yet for either checkpoint on the actual target combination** (GB10 + `dev-qwen38-next-local` / `qwen4-main-squashed`). The recommendation above is based on file-layout fit, README claims, and source-code presence — not a demonstrated working load.
- I could not determine, from SGLang's PLE-offload source alone, whether the offload mechanism truly requires PLE tensors to live in physically separate shard files (as RadixArk provides) versus being able to select tensors by name from any shard (in which case NVIDIA's bundled file might work equally well after all). This is the single biggest open technical question before treating the file-layout argument as decisive.

## Sources

- [`nvidia/Qwen3.8-Flash-Next-NVFP4`](https://huggingface.co/nvidia/Qwen3.8-Flash-Next-NVFP4) — model card / README, file tree
- [`RadixArk/Qwen3.8-Flash-Next-NVFP4`](https://huggingface.co/RadixArk/Qwen3.8-Flash-Next-NVFP4) — model card / README, file tree, `qualification-notes.md`, `conversion_environment.json`, `audit_unchanged_report.json`, `smoke_report.json`
- [`RadixArk` org profile](https://huggingface.co/RadixArk)
- [`sgl-project/sglang` PR #36497](https://github.com/sgl-project/sglang/pull/36497) — "Introduce Qwen 3.8 Flash Next" (closed, not merged)
- [`sgl-project/sglang` branch `qwen4-main-squashed` @ `4ccff141db`](https://github.com/sgl-project/sglang/tree/4ccff141dbe992794f9da6c3aa23535b4f72000d) — `python/sglang/srt/configs/qwen4_exp.py`, `python/sglang/srt/models/qwen4_exp.py`/`qwen4_exp_mtp.py`/`qwen4_exp_ple_table.py`, `python/sglang/kernels/ops/qwen4_ple.py`, `docs/cookbook/autoregressive/Qwen/Qwen3.8.mdx`
- [`sgl-project/sglang` issue #39497](https://github.com/sgl-project/sglang/issues/39497) — "errors on model type `qwen4_exp` for nvidia/Qwen3.8-Flash-Next-NVFP4" (open, unresolved)
