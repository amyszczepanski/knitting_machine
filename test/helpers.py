"""
tests/helpers.py — shared helpers for test_image.py and test_api.py.

Pytest automatically loads this file; both test modules can use
_make_png_bytes and _make_rgb_png_bytes as plain functions (imported
directly) or as fixtures if preferred.
"""

from __future__ import annotations

import io

from PIL import Image


def _make_png_bytes(
    width: int = 10,
    height: int = 10,
    color: int | tuple[int, int, int] = 128,
    mode: str = "L",
) -> bytes:
    """Return a minimal in-memory PNG as bytes."""
    img = Image.new(mode, (width, height), color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _make_rgb_png_bytes(width: int = 10, height: int = 10) -> bytes:
    return _make_png_bytes(width, height, color=(200, 100, 50), mode="RGB")
