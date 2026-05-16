"""Generate Home Screen / PWA icons for the Whisper webapp.

Draws a clean microphone glyph (white on #0a0a0a) and writes three PNGs
into ``app/webapp/static/``:

- ``icon-180.png``           — 180x180, iOS apple-touch-icon (full bleed,
                                no transparency; iOS applies its own mask).
- ``icon-512.png``           — 512x512, manifest ``purpose: any``.
- ``icon-512-maskable.png``  — 512x512, manifest ``purpose: maskable``;
                                glyph shrunk to ~60% so adaptive icon
                                masks (circle, squircle, rounded square)
                                don't crop it.
- ``favicon.ico``            — multi-size (16/32/48) browser tab icon.

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


def draw_mic(canvas_size: int, glyph_scale: float) -> Image.Image:
    """Render a mic glyph centered on a BG canvas.

    ``glyph_scale`` is the fraction of the canvas the glyph occupies
    (height of the whole mic — capsule + stand + base).
    """
    img = Image.new("RGBA", (canvas_size, canvas_size), BG)
    draw = ImageDraw.Draw(img)

    glyph_h = canvas_size * glyph_scale
    cx = canvas_size / 2
    cy = canvas_size / 2

    # Proportions tuned by eye; total height = capsule + gap + arc + base.
    capsule_h = glyph_h * 0.55
    capsule_w = capsule_h * 0.55
    arc_w = capsule_w * 1.85
    arc_h = arc_w * 0.55
    stem_h = glyph_h * 0.10
    base_w = capsule_w * 1.20
    base_h = glyph_h * 0.045

    glyph_top = cy - glyph_h / 2

    # Capsule (mic body) — rounded rect.
    cap_x0 = cx - capsule_w / 2
    cap_y0 = glyph_top
    cap_x1 = cx + capsule_w / 2
    cap_y1 = cap_y0 + capsule_h
    draw.rounded_rectangle(
        (cap_x0, cap_y0, cap_x1, cap_y1),
        radius=capsule_w / 2,
        fill=FG,
    )

    # Arc (stand cradle) — drawn as a thick open arc below the capsule.
    arc_thickness = max(4, int(canvas_size * 0.025))
    arc_y_center = cap_y1 + arc_h * 0.15
    arc_x0 = cx - arc_w / 2
    arc_y0 = arc_y_center - arc_h / 2
    arc_x1 = cx + arc_w / 2
    arc_y1 = arc_y_center + arc_h / 2
    draw.arc(
        (arc_x0, arc_y0, arc_x1, arc_y1),
        start=20,
        end=160,
        fill=FG,
        width=arc_thickness,
    )

    # Vertical stem from arc bottom down to the base.
    stem_top = arc_y1 - arc_thickness / 2
    stem_bottom = stem_top + stem_h
    stem_w = arc_thickness
    draw.rectangle(
        (cx - stem_w / 2, stem_top, cx + stem_w / 2, stem_bottom),
        fill=FG,
    )

    # Base bar.
    base_y0 = stem_bottom
    base_y1 = base_y0 + base_h
    draw.rounded_rectangle(
        (cx - base_w / 2, base_y0, cx + base_w / 2, base_y1),
        radius=base_h / 2,
        fill=FG,
    )

    return img


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    targets = [
        # (filename, canvas_size, glyph_scale)
        ("icon-180.png", 180, 0.70),
        ("icon-512.png", 512, 0.70),
        ("icon-512-maskable.png", 512, 0.60),  # safe zone for adaptive masks
    ]

    for name, size, scale in targets:
        img = draw_mic(size, scale)
        # Strip alpha for apple-touch-icon (iOS dislikes transparent pixels)
        # and to keep file sizes small. BG already opaque, so flatten to RGB.
        img.convert("RGB").save(OUT_DIR / name, format="PNG", optimize=True)
        log = logging.getLogger("gen_app_icons")
        log.info("✅ wrote %s (%dx%d)", OUT_DIR / name, size, size)

    favicon = draw_mic(256, 0.70).convert("RGB")
    favicon.save(
        OUT_DIR / "favicon.ico",
        format="ICO",
        sizes=[(16, 16), (32, 32), (48, 48)],
    )
    log.info("✅ wrote %s (16/32/48)", OUT_DIR / "favicon.ico")


if __name__ == "__main__":
    main()
