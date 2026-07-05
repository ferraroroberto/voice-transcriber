"""Shared microphone silhouette, used by both the PWA icon generator
(``scripts/gen_app_icons.py``) and the runtime tray icon (``app/gui/tray.py``)
so every surface renders the same shape.

Proportions are lifted directly from Lucide's ``mic`` icon (24x24 viewBox):
capsule ``rect x=9 y=2 w=6 h=13 rx=3``, cradle arc centred on ``(12,12) r=7``,
stem ``x=12 y=19..22`` — the same fleet convention ``home-automation``'s icon
generator used for its house glyph (issue #24 / fleet-wide app-launcher#65).
Lucide's short `v2` ticks above the arc's ends are dropped: at silhouette
scale they read as disconnected floating squares rather than hugging the
capsule.
"""

from __future__ import annotations

from PIL import Image, ImageDraw

RGBA = tuple[int, int, int, int]

# Lucide `mic` glyph, 24x24 viewBox: bounding box of capsule+cradle+stem.
_GLYPH_TOP = 2       # capsule top (rect y=2)
_GLYPH_BOTTOM = 22   # stem bottom (line y2=22)
_GLYPH_CX = 12       # horizontal center (capsule/stem/cradle all centred here)
_GLYPH_H = _GLYPH_BOTTOM - _GLYPH_TOP  # 20 units


def draw_mic(
    canvas_size: int,
    pad_ratio: float,
    fg: RGBA,
    bg: RGBA | None = None,
) -> Image.Image:
    """Render the Lucide-proportioned mic glyph centered on a canvas.

    ``pad_ratio`` is the fraction of the canvas reserved as padding on each
    side; the glyph fills the remaining safe area, scaled uniformly from
    Lucide's 24x24 coordinate space. ``bg=None`` gives a transparent canvas
    (the tray icon, colour-tinted by recording state); a solid tuple gives an
    opaque full-bleed tile (PWA/favicon/Stream Deck — required for iOS, which
    composites alpha against black and applies its own corner mask).
    """
    img = Image.new("RGBA", (canvas_size, canvas_size), bg if bg is not None else (0, 0, 0, 0))
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
        fill=fg,
    )

    # Cradle — Lucide draws this as a 2px stroke; thicken it into a solid
    # band so it stays legible at favicon/tray sizes.
    thickness = max(2, round(2.2 * scale))
    draw.arc((x(5), y(5), x(19), y(19)), start=0, end=180, fill=fg, width=thickness)

    # Stem — Lucide `M12 19v3`.
    draw.line([(x(12), y(19)), (x(12), y(22))], fill=fg, width=thickness)

    return img
