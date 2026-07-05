"""Shared microphone silhouette, used by both the PWA icon generator
(``scripts/gen_app_icons.py``) and the runtime tray icon (``app/gui/tray.py``)
so every surface renders the same shape.

Renders Lucide's ``mic`` glyph (24x24 viewBox) directly via ``resvg-py`` —
the path data below is vendored verbatim from ``project-scaffolding``'s
``brand/mic.svg`` (app-launcher#65's shared fleet icon-brand generator),
embedded here rather than read from that repo at runtime: this module is
called on every tray recording-state change, and a live cross-repo file read
on that hot path is more fragile than a one-shot dev-time generator import.
Re-copy the path data from ``project-scaffolding/brand/mic.svg`` if it ever
changes there.
"""

from __future__ import annotations

import io

import resvg_py
from PIL import Image

RGBA = tuple[int, int, int, int]

# Lucide `mic` glyph paths, 24x24 viewBox — vendored verbatim from
# project-scaffolding/brand/mic.svg.
_GLYPH_PATHS = """
    <path d="M12 19v3" />
    <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
    <rect x="9" y="2" width="6" height="13" rx="3" />
"""


def _rgba_css(color: RGBA) -> str:
    r, g, b, a = color
    return f"rgba({r},{g},{b},{a / 255:.3f})"


def draw_mic(
    canvas_size: int,
    pad_ratio: float,
    fg: RGBA,
    bg: RGBA | None = None,
) -> Image.Image:
    """Render the Lucide mic glyph centered on a canvas.

    ``pad_ratio`` is the fraction of the canvas reserved as padding on each
    side; the glyph fills the remaining safe area, scaled uniformly from
    Lucide's 24x24 coordinate space. ``bg=None`` gives a transparent canvas
    (the tray icon, colour-tinted by recording state); a solid tuple gives an
    opaque full-bleed tile (PWA/favicon/Stream Deck — required for iOS, which
    composites alpha against black and applies its own corner mask).
    """
    glyph_size = canvas_size * (1 - 2 * pad_ratio)
    offset = canvas_size * pad_ratio
    scale = glyph_size / 24
    # Fixed in the pre-scale (24-unit) coordinate space, same as
    # project-scaffolding's brand_gen.py — the transform scale below then
    # makes the rendered stroke proportionally bolder at larger canvas sizes,
    # matching the rest of the fleet's icon family.
    stroke_width = 2.6

    bg_rect = (
        f'<rect width="{canvas_size}" height="{canvas_size}" fill="{_rgba_css(bg)}"/>'
        if bg is not None
        else ""
    )
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{canvas_size}" height="{canvas_size}" viewBox="0 0 {canvas_size} {canvas_size}">
  {bg_rect}
  <g transform="translate({offset},{offset}) scale({scale})"
     fill="none" stroke="{_rgba_css(fg)}" stroke-width="{stroke_width}"
     stroke-linecap="round" stroke-linejoin="round">
    {_GLYPH_PATHS}
  </g>
</svg>"""
    png_bytes = bytes(resvg_py.svg_to_bytes(svg_string=svg, width=canvas_size, height=canvas_size))
    return Image.open(io.BytesIO(png_bytes)).convert("RGBA")
