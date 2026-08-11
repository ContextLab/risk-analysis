"""Load map artwork with real-format sniffing and strict dimension checks.

The files under ``data/raw/map_images/`` are all named ``<id>.large.jpg`` but
the *name* is not trusted: map 30 ("Tor") is actually a PNG with an alpha
channel.  Pillow sniffs the container from the magic bytes, so the extension
never matters here.  Any alpha channel is composited onto a defined opaque
background rather than silently dropped.
"""
from __future__ import annotations

import pathlib

import numpy as np
from PIL import Image

# Background used when compositing an alpha channel.  White matches the
# parchment-style artwork that actually uses alpha (map 30) and is a defined,
# reproducible choice either way.
ALPHA_BACKGROUND = (255, 255, 255)


def load_map_image(
    path: str | pathlib.Path,
    expected_size: tuple[int, int] | None = None,
) -> np.ndarray:
    """Load one map image as an RGB uint8 array of shape (H, W, 3).

    Args:
        path: image file; format is sniffed from content, not the extension.
        expected_size: catalog ``(width, height)``; a mismatch raises, because
            for this corpus the catalog dimensions are exact and a mismatch
            means a corrupt or wrong download.
    """
    path = pathlib.Path(path)
    with Image.open(path) as im:
        im.load()
        if im.mode in ("RGBA", "LA", "PA") or (
            im.mode == "P" and "transparency" in im.info
        ):
            rgba = im.convert("RGBA")
            background = Image.new("RGB", rgba.size, ALPHA_BACKGROUND)
            background.paste(rgba, mask=rgba.getchannel("A"))
            rgb = background
        else:
            rgb = im.convert("RGB")
        if expected_size is not None and rgb.size != tuple(expected_size):
            raise ValueError(
                f"{path.name}: loaded size {rgb.size} != catalog size "
                f"{tuple(expected_size)}; the download is bad"
            )
        return np.asarray(rgb, dtype=np.uint8)


def sniff_format(path: str | pathlib.Path) -> str:
    """Return Pillow's detected container format (e.g. 'JPEG', 'PNG')."""
    with Image.open(path) as im:
        return im.format or "unknown"
