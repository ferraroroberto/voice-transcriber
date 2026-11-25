"""Fetch the whisper.cpp server and the ggml model into `vendor/whisper.cpp/`.

The installer's dependency step (`[install.deps]` in `.fleet.toml`): it runs the
two fetch scripts in order. Each returns at once when its file is already in
place, so a rerun costs nothing.

Usage:
    python scripts/setup_assets.py
"""

from __future__ import annotations

import sys

# Run as `python scripts/setup_assets.py`, sys.path[0] is scripts/, so the two
# fetch scripts import by their bare names.
import download_model
import install_whisper_cpp


def main() -> int:
    return install_whisper_cpp.main() or download_model.main()


if __name__ == "__main__":
    sys.exit(main())
