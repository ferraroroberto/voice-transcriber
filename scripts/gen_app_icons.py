"""Generate the static app-icon family from the shared fleet brand generator.

The canonical master is ``project-scaffolding/brand/mic.svg``. The live tray
keeps its separate runtime renderer because it tints the same microphone
silhouette by recording state; this script owns only file-backed surfaces:
PWA, favicon, Stream Deck, and shortcut/Explorer tray assets.

Usage:
    & .\\.venv\\Scripts\\python.exe scripts\\gen_app_icons.py
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCAFFOLDING_ROOT = Path(
    os.environ.get("PROJECT_SCAFFOLDING_ROOT", r"E:\automation\project-scaffolding")
)
sys.path.insert(0, str(SCAFFOLDING_ROOT / "scripts"))

from brand_gen import render_set  # noqa: E402

STATIC_DIR = PROJECT_ROOT / "app" / "webapp" / "static"

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("gen_app_icons")


def main() -> None:
    render_set(
        master=SCAFFOLDING_ROOT / "brand" / "mic.svg",
        out_dir=STATIC_DIR,
        tray_out_dir=PROJECT_ROOT / "assets" / "tray",
        stream_deck_out_dir=PROJECT_ROOT / "assets" / "stream-deck",
        project_slug="voice-transcriber",
    )
    log.info("✅ wrote canonical icon family to %s", STATIC_DIR)


if __name__ == "__main__":
    main()
