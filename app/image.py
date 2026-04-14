"""
app/image.py — Image loading and preprocessing for the Brother KH-930/940.

Loads a 1-bit (or any) image via Pillow, scales/crops to fit within
MAX_NEEDLES (200) columns, converts to 1-bit, and returns a list of
rows, each row a list of ints (0 = background/skip, 1 = knit).

Public API
----------
load_image(source) -> ImageResult
    source: path-like, bytes, or a Pillow Image.
    Returns an ImageResult dataclass.

ImageResult
    .rows       : list[list[int]]  — pixel rows, each len == width
    .width      : int
    .height     : int
    .orig_width : int
    .orig_height: int

Errors
------
ImageError   raised for unsupported files or images that can't be reduced
             to a non-zero size within the needle constraint.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path
from typing import Union

from PIL import Image, UnidentifiedImageError

MAX_NEEDLES: int = 200  # KH-940 physical needle count

# Default stitch aspect ratio correction.  Knit stitches are approximately
# 4 units wide × 3 units tall, so a 1:1 pixel image knitted stitch-for-stitch
# will appear squashed vertically by roughly 1/1.33.  Stretching the image
# vertically by this factor before binarising compensates for that distortion.
DEFAULT_STITCH_ASPECT_RATIO: float = 4 / 3  # ≈ 1.333

# Pillow resample filter — LANCZOS gives best quality for downscaling
_RESAMPLE = Image.Resampling.LANCZOS


class ImageError(ValueError):
    """Raised when an image cannot be processed for the knitting machine."""


@dataclass(frozen=True)
class ImageResult:
    """Processed image ready for Brother format encoding."""

    rows: list[list[int]]
    width: int
    height: int
    orig_width: int
    orig_height: int


def load_image(
    source: Union[str, Path, bytes, "Image.Image"],
    *,
    max_width: int = MAX_NEEDLES,
    max_rows: int | None = None,
    threshold: int = 128,
    stitch_aspect_ratio: float = DEFAULT_STITCH_ASPECT_RATIO,
    crop: tuple[int, int, int, int] | None = None,
) -> ImageResult:
    """Load, scale, optionally crop, and binarise an image.

    Parameters
    ----------
    source:
        A file path, raw image bytes, or an already-open Pillow Image.
    max_width:
        Maximum output width in pixels/stitches (default 200).
    max_rows:
        Maximum output height in rows.  When set, the image is scaled down
        proportionally (preserving aspect ratio) if the height after the
        stitch-aspect-ratio stretch would exceed this limit.  Defaults to
        None (no row limit).  Pass DiskImage.max_rows to enforce the target
        machine's memory capacity.
    threshold:
        Greyscale threshold for the 1-bit conversion [0–255].
        Pixels ≤ threshold become 1 (knit); pixels > threshold become 0.
        Default 128 (midpoint).
    stitch_aspect_ratio:
        Vertical stretch factor applied after width-scaling to compensate
        for the non-square aspect ratio of knit stitches.  A value of 1.33
        (the default, equal to 4/3) corrects for stitches that are
        approximately 4 units wide × 3 units tall — without this correction
        a square source image would appear squashed vertically when knitted.
        Set to 1.0 to disable aspect-ratio correction entirely.
    crop:
        Optional (left, upper, right, lower) box applied *before* scaling,
        in original-image coordinates.  Useful when the caller has already
        determined a region of interest.

    Returns
    -------
    ImageResult

    Raises
    ------
    ImageError
        If the file cannot be read, is not a recognised image format, or
        the resulting image would have zero stitches in either dimension,
        or the image cannot be scaled to fit within max_rows.
    ValueError
        If stitch_aspect_ratio is not a positive number, or max_rows is
        not a positive integer.
    """
    if stitch_aspect_ratio <= 0:
        raise ValueError(
            f"stitch_aspect_ratio must be positive, got {stitch_aspect_ratio}"
        )
    if max_rows is not None and max_rows < 1:
        raise ValueError(f"max_rows must be a positive integer, got {max_rows}")

    img = _open(source)
    orig_width, orig_height = img.size

    # --- optional crop (in original-image space) ---
    if crop is not None:
        left, upper, right, lower = crop
        left = max(0, left)
        upper = max(0, upper)
        right = min(orig_width, right)
        lower = min(orig_height, lower)
        if right <= left or lower <= upper:
            raise ImageError(
                f"Crop box {crop!r} yields an empty region "
                f"for image of size {img.size}."
            )
        img = img.crop((left, upper, right, lower))

    # --- convert to greyscale before any scaling ---
    img = img.convert("L")

    # --- scale so width ≤ max_width, preserving aspect ratio ---
    w, h = img.size
    if w > max_width:
        scale = max_width / w
        new_w = max_width
        new_h = max(1, round(h * scale))
        img = img.resize((new_w, new_h), _RESAMPLE)
        w, h = img.size

    # --- apply stitch aspect-ratio correction (vertical stretch) ---
    # This compensates for knit stitches being wider than they are tall.
    # The resize is done after the width-constraining step so that
    # max_width still refers to the stitch count, not the pixel count.
    if stitch_aspect_ratio != 1.0:
        new_h = max(1, round(h * stitch_aspect_ratio))
        img = img.resize((w, new_h), _RESAMPLE)
        h = new_h

    # --- enforce max_rows (machine memory limit) ---
    # Applied after the aspect-ratio stretch so that max_rows refers to
    # knitted rows, not source pixels.  We scale width proportionally to
    # preserve the design's aspect ratio.
    if max_rows is not None and h > max_rows:
        scale = max_rows / h
        new_w = max(1, round(w * scale))
        new_h = max_rows
        img = img.resize((new_w, new_h), _RESAMPLE)
        w, h = img.size

    if w == 0 or h == 0:
        raise ImageError(
            f"Image reduced to zero size (got {w}×{h}). "
            "Check that the source image is not empty."
        )

    # --- binarise: pixels ≤ threshold → 1 (knit), else → 0 ---
    rows: list[list[int]] = []
    pixels = img.load()
    for y in range(h):
        row: list[int] = []
        for x in range(w):
            lum: int = pixels[x, y]  # type: ignore[index, assignment]
            row.append(1 if lum <= threshold else 0)
        rows.append(row)

    return ImageResult(
        rows=rows,
        width=w,
        height=h,
        orig_width=orig_width,
        orig_height=orig_height,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _open(source: Union[str, Path, bytes, "Image.Image"]) -> "Image.Image":
    """Normalise *source* to a PIL Image."""
    if isinstance(source, Image.Image):
        return source.copy()

    if isinstance(source, bytes):
        try:
            return Image.open(io.BytesIO(source))
        except UnidentifiedImageError as exc:
            raise ImageError(f"Cannot identify image format: {exc}") from exc
        except Exception as exc:
            raise ImageError(f"Failed to open image from bytes: {exc}") from exc

    path = Path(source)
    if not path.exists():
        raise ImageError(f"File not found: {path}")
    try:
        return Image.open(path)
    except UnidentifiedImageError as exc:
        raise ImageError(f"Cannot identify image format for {path}: {exc}") from exc
    except Exception as exc:
        raise ImageError(f"Failed to open {path}: {exc}") from exc
