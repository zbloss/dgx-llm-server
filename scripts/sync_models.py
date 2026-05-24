#!/usr/bin/env python3
"""
Syncs model files on the DGX Spark with the manifest in models/models.json.
- Downloads any model listed in the manifest that is not yet on disk.
- Removes any .gguf file on disk that is no longer listed in the manifest.

Run by the GitHub Actions self-hosted runner after each push that touches
models/models.json or models/config.ini.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

MODELS_DIR = Path(os.environ.get("MODELS_DIR", "/home/zbloss/models"))
MANIFEST = Path(__file__).parent.parent / "models" / "models.json"


def load_manifest():
    with open(MANIFEST) as f:
        return json.load(f)["models"]


def filenames(model: dict) -> list[str]:
    """Normalise hf_filename (str) or hf_filenames (list) to a list."""
    if "hf_filenames" in model:
        return model["hf_filenames"]
    return [model["hf_filename"]]


def download_file(repo: str, filename: str) -> None:
    print(f"[download] {repo}/{filename}")
    cmd = [
        "huggingface-cli", "download",
        repo,
        filename,
        "--local-dir", str(MODELS_DIR),
        "--local-dir-use-symlinks", "False",
    ]
    token = os.environ.get("HF_TOKEN")
    if token:
        cmd += ["--token", token]
    subprocess.run(cmd, check=True)


def sync() -> None:
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    models = load_manifest()
    desired = {f for m in models for f in filenames(m)}

    for model in models:
        for fname in filenames(model):
            if (MODELS_DIR / fname).exists():
                print(f"[ok] {fname}")
            else:
                download_file(model["hf_repo"], fname)

    for gguf in sorted(MODELS_DIR.glob("*.gguf")):
        if gguf.name not in desired:
            print(f"[remove] {gguf.name}")
            gguf.unlink()


if __name__ == "__main__":
    try:
        sync()
    except subprocess.CalledProcessError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)
