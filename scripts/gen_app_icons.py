"""Generate Home Screen / PWA / Stream Deck / tray icons for the Whisper webapp.

Draws a solid microphone silhouette from the shared glyph in
``src/mic_glyph.py`` (same shape the runtime tray icon in ``app/gui/tray.py``
uses, just recoloured — see that module's docstring for where the proportions
come from). Writes:

- ``app/webapp/static/icon-180.png``           — iOS apple-touch-icon.
- ``app/webapp/static/icon-192.png``           — Android manifest icon.
- ``app/webapp/static/icon-512.png``           — manifest ``purpose: any``.
- ``app/webapp/static/icon-512-maskable.png``  — manifest ``purpose:
  maskable``; glyph shrunk so adaptive icon masks (circle, squircle, rounded
  square) don't crop it.
- ``app/webapp/static/favicon.ico``            — multi-size (16/32/48)
  browser tab icon.
- ``assets/stream-deck/voice-transcriber-144.png`` — Elgato Stream Deck
  button, full bleed.
- ``assets/tray/voice-transcriber.ico``         — Windows Explorer / shortcut
  icon source (multi-size). The *live* tray icon is still drawn at runtime by
  ``app/gui/tray.py`` so it can recolour by recording state; this static file
  is for surfaces that need a fixed file (pinned shortcuts, Start Menu).

Run from the repo root:

    & .\\.venv\\Scripts\\python.exe scripts\\gen_app_icons.py
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.mic_glyph import draw_mic  # noqa: E402  — sys.path tweak above

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("gen_app_icons")

STATIC_DIR = PROJECT_ROOT / "app" / "webapp" / "static"
STREAM_DECK_DIR = PROJECT_ROOT / "assets" / "stream-deck"
TRAY_DIR = PROJECT_ROOT / "assets" / "tray"

BG = (10, 10, 10, 255)        # #0a0a0a — matches theme_color
FG = (245, 245, 245, 255)     # near-white glyph


def main() -> None:
    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    STREAM_DECK_DIR.mkdir(parents=True, exist_ok=True)
    TRAY_DIR.mkdir(parents=True, exist_ok=True)

    pwa_targets = [
        # (filename, canvas_size, pad_ratio)
        ("icon-180.png", 180, 0.15),
        ("icon-192.png", 192, 0.15),
        ("icon-512.png", 512, 0.15),
        ("icon-512-maskable.png", 512, 0.20),  # safe zone for adaptive masks
    ]

    for name, size, pad_ratio in pwa_targets:
        img = draw_mic(size, pad_ratio, fg=FG, bg=BG)
        # Strip alpha for apple-touch-icon (iOS dislikes transparent pixels)
        # and to keep file sizes small. BG already opaque, so flatten to RGB.
        img.convert("RGB").save(STATIC_DIR / name, format="PNG", optimize=True)
        log.info("✅ wrote %s (%dx%d)", STATIC_DIR / name, size, size)

    favicon = draw_mic(256, 0.15, fg=FG, bg=BG).convert("RGB")
    favicon.save(
        STATIC_DIR / "favicon.ico",
        format="ICO",
        sizes=[(16, 16), (32, 32), (48, 48)],
    )
    log.info("✅ wrote %s (16/32/48)", STATIC_DIR / "favicon.ico")

    stream_deck = draw_mic(144, 0.15, fg=FG, bg=BG).convert("RGB")
    stream_deck_path = STREAM_DECK_DIR / "voice-transcriber-144.png"
    stream_deck.save(stream_deck_path, format="PNG", optimize=True)
    log.info("✅ wrote %s (144x144)", stream_deck_path)

    tray_ico = draw_mic(256, 0.15, fg=FG, bg=BG).convert("RGB")
    tray_ico_path = TRAY_DIR / "voice-transcriber.ico"
    tray_ico.save(
        tray_ico_path,
        format="ICO",
        sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (256, 256)],
    )
    log.info("✅ wrote %s (16/32/48/64/256)", tray_ico_path)


if __name__ == "__main__":
    main()
