"""Download a ggml whisper model into `vendor/whisper.cpp/models/`.

Thin wrapper over `huggingface_hub.hf_hub_download`. The default model
matches the config (`ggml-large-v3-turbo.bin`, 1.62 GB) — pass `--model`
to fetch a different size.

Usage:
    python scripts/download_model.py
    python scripts/download_model.py --model ggml-small.bin
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DIR = PROJECT_ROOT / "vendor" / "whisper.cpp" / "models"
DEFAULT_MODEL = "ggml-large-v3-turbo.bin"
HF_REPO = "ggerganov/whisper.cpp"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--model", default=DEFAULT_MODEL,
        help=f"GGML model filename in {HF_REPO} (default: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--dest", default=str(DEFAULT_DIR),
        help="Destination directory (default: vendor/whisper.cpp/models)",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        from huggingface_hub import hf_hub_download
    except ImportError:
        log.error("huggingface_hub is not installed. Run `pip install -r requirements.txt`.")
        return 1

    dest = Path(args.dest).resolve()
    dest.mkdir(parents=True, exist_ok=True)
    expected = dest / args.model
    if expected.exists():
        log.info("Model already present at %s", expected)
        return 0

    log.info("Downloading %s from %s → %s", args.model, HF_REPO, dest)
    local_path = hf_hub_download(
        repo_id=HF_REPO,
        filename=args.model,
        local_dir=str(dest),
    )
    log.info("Downloaded → %s", local_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
