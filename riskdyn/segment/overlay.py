"""Human-review overlay: boundaries + numbered centroids over the artwork.

Every map gets inspected by a human, so the overlay optimises for making an
error obvious at a glance:

- each territory outline is a bright stroke cased in black, visible against
  any palette (near-white Arctic ice and dark oceans alike);
- each centroid carries the territory index, white-on-dark disc;
- flagged territories (from geometry or the report) get a red halo.
"""
from __future__ import annotations

import pathlib

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from riskdyn.segment.geometry import TerritoryShape

_FONT_CANDIDATES = (
    "/System/Library/Fonts/Helvetica.ttc",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
)


def _font(size: int) -> ImageFont.ImageFont:
    for path in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def draw_overlay(
    image: np.ndarray,
    shapes: list[TerritoryShape],
    path: str | pathlib.Path,
    flagged: dict[int, str] | None = None,
    header: str | None = None,
) -> None:
    """Write the overlay PNG.

    Args:
        image: original artwork, RGB uint8.
        shapes: extracted territories.
        path: output PNG path.
        flagged: {territory index: reason} for anything needing attention;
            territory-level ``flags`` are merged in automatically.
        header: one-line banner (e.g. "map 1 World Classic: 42/42 expected").
    """
    flagged = dict(flagged or {})
    for s in shapes:
        if s.flags and s.index not in flagged:
            flagged[s.index] = ",".join(s.flags)

    im = Image.fromarray(image).convert("RGB")
    draw = ImageDraw.Draw(im, "RGBA")
    font = _font(15)
    small = _font(12)

    for s in shapes:
        for poly in s.polygons:
            pts = [(x, y) for x, y in poly]
            if len(pts) >= 3:
                draw.line(pts + [pts[0]], fill=(0, 0, 0, 255), width=4)
    for s in shapes:
        color = (255, 64, 64, 255) if s.index in flagged else (255, 255, 0, 255)
        for poly in s.polygons:
            pts = [(x, y) for x, y in poly]
            if len(pts) >= 3:
                draw.line(pts + [pts[0]], fill=color, width=2)

    for s in shapes:
        cx, cy = s.centroid
        r = 11 if s.index in flagged else 9
        if s.index in flagged:
            draw.ellipse((cx - r - 4, cy - r - 4, cx + r + 4, cy + r + 4),
                         outline=(255, 0, 0, 255), width=3)
        draw.ellipse((cx - r, cy - r, cx + r, cy + r),
                     fill=(20, 20, 20, 230), outline=(255, 255, 255, 255), width=1)
        text = str(s.index)
        f = font if len(text) <= 2 else small
        tw = draw.textlength(text, font=f)
        draw.text((cx - tw / 2, cy - (f.size / 2) - 1), text,
                  fill=(255, 255, 255, 255), font=f)

    if header:
        f = _font(18)
        tw = draw.textlength(header, font=f)
        draw.rectangle((0, 0, tw + 16, 28), fill=(0, 0, 0, 200))
        draw.text((8, 4), header, fill=(255, 255, 100, 255), font=f)

    im.save(path, format="PNG")
