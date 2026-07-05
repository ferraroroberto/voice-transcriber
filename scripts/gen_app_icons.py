"""Generate Home Screen / PWA icons for the Whisper webapp.

Draws a solid microphone silhouette (near-white on #0a0a0a) and writes three
PNGs into ``app/webapp/static/``:

- ``icon-180.png``           — 180x180, iOS apple-touch-icon (full bleed,
                                no transparency; iOS applies its own mask).
- ``icon-512.png``           — 512x512, manifest ``purpose: any``.
- ``icon-512-maskable.png``  — 512x512, manifest ``purpose: maskable``;
                                glyph shrunk so adaptive icon masks (circle,
                                squircle, rounded square) don't crop it.
- ``favicon.ico``            — multi-size (16/32/48) browser tab icon.

The glyph's proportions are lifted directly from Lucide's ``mic`` icon
(24x24 viewBox: capsule ``rect x=9 y=2 w=6 h=13 rx=3``, cradle arc centred
on ``(12,12) r=7``, stem ``x=12 y=19..22``) — same fleet convention
``home-automation/scripts/gen_icons.py`` used for its house glyph, so the
PWA icon reads as the solid-silhouette sibling of the in-app icon family
rather than an independently eyeballed shape (issue #24 / fleet-wide
app-launcher#65).

Run from the repo root:

    & .\\.venv\\Scripts\\python.exe scripts\\gen_app_icons.py
"""

from __future__ import annotations

import logging
from pathlib import Path

from PIL import Image, ImageDraw

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("gen_app_icons")

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = REPO_ROOT / "app" / "webapp" / "static"

BG = (10, 10, 10, 255)        # #0a0a0a — matches theme_color
FG = (245, 245, 245, 255)     # near-white glyph

# Lucide `mic` glyph, 24x24 viewBox: bounding box of capsule+cradle+stem.
_GLYPH_TOP = 2       # capsule top (rect y=2)
_GLYPH_BOTTOM = 22   # stem bottom (line y2=22)
_GLYPH_CX = 12       # horizontal center (capsule/stem/cradle all centred here)
_GLYPH_H = _GLYPH_BOTTOM - _GLYPH_TOP  # 20 units


def draw_mic(canvas_size: int, pad_ratio: float) -> Image.Image:
    """Render the Lucide-proportioned mic glyph centered on a BG canvas.

    ``pad_ratio`` is the fraction of the canvas reserved as padding on each
    side; the glyph fills the remaining safe area, scaled uniformly from
    Lucide's 24x24 coordinate space.
    """
    img = Image.new("RGBA", (canvas_size, canvas_size), BG)
    draw = ImageDraw.Draw(img)

    safe = canvas_size * (1 - 2 * pad_ratio)
    scale = safe / _GLYPH_H
    top = canvas_size * pad_ratio
    cx = canvas_size / 2

    def x(lucide_x: float) -> float:
        return cx + (lucide_x - _GLYPH_CX) * scale

    def y(lucide_y: float) -> float:
        return top + (lucide_y - _GLYPH_TOP) * scale

    # Capsule (mic body) — Lucide `rect x=9 y=2 width=6 height=13 rx=3`.
    draw.rounded_rectangle(
        (x(9), y(2), x(15), y(15)),
        radius=3 * scale,
        fill=FG,
    )

    # Cradle — Lucide draws this as a 2px stroke; thicken it into a solid
    # band so it stays legible at favicon sizes. Lucide's short `v2` ticks
    # above the arc's ends are dropped — at silhouette scale they read as
    # disconnected floating squares rather than hugging the capsule.
    thickness = max(2, round(2.2 * scale))
    draw.arc((x(5), y(5), x(19), y(19)), start=0, end=180, fill=FG, width=thickness)

    # Stem — Lucide `M12 19v3`.
    draw.line([(x(12), y(19)), (x(12), y(22))], fill=FG, width=thickness)

    return img


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    targets = [
        # (filename, canvas_size, pad_ratio)
        ("icon-180.png", 180, 0.15),
        ("icon-512.png", 512, 0.15),
        ("icon-512-maskable.png", 512, 0.20),  # safe zone for adaptive masks
    ]

    for name, size, pad_ratio in targets:
        img = draw_mic(size, pad_ratio)
        # Strip alpha for apple-touch-icon (iOS dislikes transparent pixels)
        # and to keep file sizes small. BG already opaque, so flatten to RGB.
        img.convert("RGB").save(OUT_DIR / name, format="PNG", optimize=True)
        log.info("✅ wrote %s (%dx%d)", OUT_DIR / name, size, size)

    favicon = draw_mic(256, 0.15).convert("RGB")
    favicon.save(
        OUT_DIR / "favicon.ico",
        format="ICO",
        sizes=[(16, 16), (32, 32), (48, 48)],
    )
    log.info("✅ wrote %s (16/32/48)", OUT_DIR / "favicon.ico")


if __name__ == "__main__":
    main()
