"""
app/image.py — Image loading and preprocessing for the Brother KH-930/940.

Loads a 1-bit (or any) image via Pillow, scales/crops to fit within
MAX_NEEDLES (200) columns, converts to 1-bit, and returns a list of
rows, each row a list of ints (0 = background/skip, 1 = knit).

Public API
----------
load_image(source, **kwargs) -> ImageResult
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
from typing import Literal, Union, cast

from PIL import Image, UnidentifiedImageError

MAX_NEEDLES: int = 200  # KH-940 physical needle count

# Default stitch aspect ratio correction.  Knit stitches are approximately
# 4 units wide × 3 units tall, so a 1:1 pixel image knitted stitch-for-stitch
# will appear squashed vertically by roughly 1/1.33.  Stretching the image
# vertically by this factor before binarising compensates for that distortion.
DEFAULT_STITCH_ASPECT_RATIO: float = 4 / 3  # ≈ 1.333

# Pillow resample filter — LANCZOS gives best quality for downscaling
_RESAMPLE = Image.Resampling.LANCZOS

# Bayer 4×4 ordered dithering matrix (values 0–15, normalised to 0–255 below).
# Each entry is a threshold: if the pixel luminance > threshold, output white.
_BAYER_4X4: list[list[int]] = [
    [0, 136, 34, 170],
    [204, 68, 238, 102],
    [51, 187, 17, 153],
    [255, 119, 221, 85],
]

# Valid rotation values (degrees clockwise).
Rotation = Literal[0, 90, 180, 270]

# Dithering algorithm names accepted by load_image.
DitherMode = Literal["none", "floyd-steinberg", "bayer"]


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
    flip_horizontal: bool = False,
    rotation: Rotation = 0,
    invert: bool = False,
    dither: DitherMode = "none",
) -> ImageResult:
    """Load, scale, optionally crop/flip/rotate, and binarise an image.

    Parameters
    ----------
    source:
        A file path, raw image bytes, or an already-open Pillow Image.
    max_width:
        Maximum output width in pixels/stitches (default 200).
    max_rows:
        Maximum output height in rows.  When set, the image is scaled down
        proportionally if the height after the stitch-aspect-ratio stretch
        would exceed this limit.  Defaults to None (no row limit).
    threshold:
        Greyscale threshold for the 1-bit conversion [0–255].
        Pixels ≤ threshold become 1 (knit); pixels > threshold become 0.
        Ignored when dither is not "none".
    stitch_aspect_ratio:
        Vertical stretch factor to compensate for non-square stitch aspect
        ratio.  Default 4/3 (≈1.333).  Set to 1.0 to disable.
    crop:
        Optional (left, upper, right, lower) box in original-image coordinates,
        applied before any other transformation.
    flip_horizontal:
        If True, mirror the image left-to-right before scaling.  Applied after
        crop, before rotation.
    rotation:
        Clockwise rotation in degrees: 0, 90, 180, or 270.  Applied after flip,
        before greyscale conversion and scaling.
    invert:
        If True, swap knit (1) and background (0) in the final binary output.
        Applied after binarisation.
    dither:
        Binarisation method.  One of:
          "none"            — hard threshold (default).
          "floyd-steinberg" — error-diffusion dithering via Pillow; works well
                              for photos and smooth gradients.
          "bayer"           — 4×4 ordered (Bayer) dithering; produces a regular
                              crosshatch pattern, good for geometric designs.

    Returns
    -------
    ImageResult

    Raises
    ------
    ImageError
        If the file cannot be read, is not a recognised image format, the
        resulting image would have zero stitches in either dimension, or the
        image cannot be scaled to fit within max_rows.
    ValueError
        If stitch_aspect_ratio is not positive, max_rows is not a positive
        integer, or rotation is not one of {0, 90, 180, 270}.
    """
    if stitch_aspect_ratio <= 0:
        raise ValueError(
            f"stitch_aspect_ratio must be positive, got {stitch_aspect_ratio}"
        )
    if max_rows is not None and max_rows < 1:
        raise ValueError(f"max_rows must be a positive integer, got {max_rows}")
    if rotation not in (0, 90, 180, 270):
        raise ValueError(f"rotation must be 0, 90, 180, or 270; got {rotation}")

    img = _open(source)
    orig_width, orig_height = img.size

    # --- 1. Crop (in original-image space) ---
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

    # --- 2. Flip horizontal ---
    if flip_horizontal:
        img = img.transpose(Image.Transpose.FLIP_LEFT_RIGHT)

    # --- 3. Rotate (clockwise; Pillow rotates counter-clockwise, so negate) ---
    if rotation != 0:
        img = img.rotate(-rotation, expand=True)

    # --- 4. Convert to greyscale before scaling ---
    img = img.convert("L")

    # --- 5. Scale so width ≤ max_width, preserving aspect ratio ---
    w, h = img.size
    if w > max_width:
        scale = max_width / w
        new_w = max_width
        new_h = max(1, round(h * scale))
        img = img.resize((new_w, new_h), _RESAMPLE)
        w, h = img.size

    # --- 6. Apply stitch aspect-ratio correction (vertical stretch) ---
    if stitch_aspect_ratio != 1.0:
        new_h = max(1, round(h * stitch_aspect_ratio))
        img = img.resize((w, new_h), _RESAMPLE)
        h = new_h

    # --- 7. Enforce max_rows ---
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

    # --- 8. Binarise ---
    rows: list[list[int]] = _binarise(img, w, h, dither, threshold)

    # --- 9. Invert (swap knit ↔ background) ---
    if invert:
        rows = [[1 - v for v in row] for row in rows]

    return ImageResult(
        rows=rows,
        width=w,
        height=h,
        orig_width=orig_width,
        orig_height=orig_height,
    )


# ---------------------------------------------------------------------------
# Binarisation helpers
# ---------------------------------------------------------------------------


def _binarise(
    img: "Image.Image",
    w: int,
    h: int,
    dither: DitherMode,
    threshold: int,
) -> list[list[int]]:
    """Convert a greyscale Pillow image to a list-of-lists of 0/1 values."""
    if dither == "floyd-steinberg":
        return _binarise_floyd_steinberg(img, w, h)
    elif dither == "bayer":
        return _binarise_bayer(img, w, h)
    else:
        return _binarise_threshold(img, w, h, threshold)


def _binarise_threshold(
    img: "Image.Image", w: int, h: int, threshold: int
) -> list[list[int]]:
    """Hard threshold: pixels ≤ threshold → 1 (knit), else → 0."""
    pixels = img.load()
    rows: list[list[int]] = []
    for y in range(h):
        row: list[int] = []
        for x in range(w):
            lum: int = cast(int, pixels[x, y])  # type: ignore[index]
            row.append(1 if lum <= threshold else 0)
        rows.append(row)
    return rows


def _binarise_floyd_steinberg(img: "Image.Image", w: int, h: int) -> list[list[int]]:
    """Error-diffusion dithering using Pillow's built-in Floyd-Steinberg.

    Pillow's Image.convert("1") uses Floyd-Steinberg by default.
    The resulting 1-bit image maps black (0) → knit (1), white (255) → skip (0).
    """
    dithered = img.convert("1")  # Pillow applies Floyd-Steinberg here
    pixels = dithered.load()
    rows: list[list[int]] = []
    for y in range(h):
        row: list[int] = []
        for x in range(w):
            # Pillow "1" mode: pixel value is 0 (black) or 255 (white)
            row.append(1 if pixels[x, y] == 0 else 0)  # type: ignore[index]
        rows.append(row)
    return rows


def _binarise_bayer(img: "Image.Image", w: int, h: int) -> list[list[int]]:
    """Ordered (Bayer 4×4) dithering.

    Each pixel's luminance is compared against a spatially-varying threshold
    from the Bayer matrix, tiled across the image.  Produces a regular
    crosshatch pattern that preserves overall tone without error propagation.
    """
    pixels = img.load()
    rows: list[list[int]] = []
    for y in range(h):
        row: list[int] = []
        for x in range(w):
            lum: int = cast(int, pixels[x, y])  # type: ignore[index]
            bayer_threshold = _BAYER_4X4[y % 4][x % 4]
            row.append(1 if lum <= bayer_threshold else 0)
        rows.append(row)
    return rows


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
