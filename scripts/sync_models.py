#!/usr/bin/env python3
"""
Syncs GGUF model repos on the DGX Spark with the manifest in models/models.json.
- Downloads any HuggingFace repo listed in the manifest that is not yet on disk.
- Removes any model directory on disk that is no longer listed in the manifest.

Run by the GitHub Actions self-hosted runner after each push that touches
models/models.json, models/config.ini, or compose.yaml.
"""
import json
import os
import shutil
import sys
from pathlib import Path

from huggingface_hub import snapshot_download

MODELS_DIR = Path(os.environ.get("MODELS_DIR", "/home/zbloss/models"))
MANIFEST = Path(__file__).parent.parent / "models" / "models.json"


def load_manifest():
    with open(MANIFEST) as f:
        return json.load(f)["models"]


def local_dir(hf_repo: str) -> Path:
    return MODELS_DIR / hf_repo.replace("/", "--")


def sync() -> None:
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    models = load_manifest()
    desired = {local_dir(m["hf_repo"]).name for m in models}

    for model in models:
        dest = local_dir(model["hf_repo"])
        if dest.exists():
            print(f"[ok] {dest.name}")
        else:
            print(f"[download] {model['hf_repo']} → {dest}")
            snapshot_download(
                repo_id=model["hf_repo"],
                local_dir=str(dest),
                token=os.environ.get("HF_TOKEN"),
                allow_patterns=model.get("allow_patterns"),
            )

    for entry in sorted(MODELS_DIR.iterdir()):
        if entry.is_dir() and entry.name not in desired:
            print(f"[remove] {entry.name}")
            shutil.rmtree(entry)
        elif entry.is_file() and entry.suffix == ".gguf":
            print(f"[remove] {entry.name}")
            entry.unlink()


if __name__ == "__main__":
    try:
        sync()
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)
