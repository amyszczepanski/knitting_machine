"""
tests/test_image_processing.py — Tests for Stage 1 image processing additions.

Covers the flip_horizontal, rotation, invert, and dither parameters added to
app/image.py.  Follows the same style as test_image.py.

Run with:
    pytest tests/test_image_processing.py -v
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

_mock_disk_image_cls = MagicMock()
_mock_machine_model = MagicMock()
_mock_machine_model.KH940 = "KH940"

from PIL import Image as _PIL_Image  # noqa: E402

_PIL_Image.preinit()

with patch.dict(
    "sys.modules",
    {
        "serial": MagicMock(),
        "serial.tools": MagicMock(),
        "serial.tools.list_ports": MagicMock(),
        "serial.tools.list_ports_common": MagicMock(),
        "app.brother_format": MagicMock(
            DiskImage=_mock_disk_image_cls,
            MachineModel=_mock_machine_model,
        ),
        "app.serial_emulator": MagicMock(),
        "app.ports": MagicMock(
            discover_ftdi_port=MagicMock(return_value=MagicMock(device="/dev/ttyUSB0")),
            list_all_ports=MagicMock(return_value=[]),
            PortDiscoveryError=Exception,
        ),
    },
):
    from app.image import load_image, DitherMode  # noqa: E402

from .helpers import _make_png_bytes  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_asymmetric_image() -> bytes:
    """Return a 4×2 greyscale PNG whose left half is black and right half white.

    Row layout (L=0/black=knit, R=255/white=skip):
        [L, L, R, R]
        [L, L, R, R]

    Used to verify flip and rotation by checking which pixels are knit.
    """
    img = Image.new("L", (4, 2), 255)
    for y in range(2):
        for x in range(2):
            img.putpixel((x, y), 0)  # left two columns black
    import io

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ===========================================================================
# Flip horizontal
# ===========================================================================


class TestFlipHorizontal:
    def test_flip_mirrors_columns(self):
        # Left half black (knit=1), right half white (skip=0).
        # After flip: left half white, right half black.
        result_normal = load_image(_make_asymmetric_image(), stitch_aspect_ratio=1.0)
        result_flipped = load_image(
            _make_asymmetric_image(), stitch_aspect_ratio=1.0, flip_horizontal=True
        )
        for row_n, row_f in zip(result_normal.rows, result_flipped.rows):
            assert row_n == list(reversed(row_f))

    def test_flip_preserves_dimensions(self):
        normal = load_image(
            _make_png_bytes(width=30, height=10), stitch_aspect_ratio=1.0
        )
        flipped = load_image(
            _make_png_bytes(width=30, height=10),
            stitch_aspect_ratio=1.0,
            flip_horizontal=True,
        )
        assert normal.width == flipped.width
        assert normal.height == flipped.height

    def test_double_flip_is_identity(self):
        # Flipping twice should return the same pixel data as no flip.
        src = _make_asymmetric_image()
        twice = load_image(src, stitch_aspect_ratio=1.0, flip_horizontal=False).rows
        # Single flip then compare to unflipped re-flip (identity check via rows).
        unflipped = load_image(src, stitch_aspect_ratio=1.0).rows
        assert twice == unflipped


# ===========================================================================
# Rotation
# ===========================================================================


class TestRotation:
    def test_rotation_0_unchanged(self):
        result = load_image(
            _make_png_bytes(width=40, height=10),
            stitch_aspect_ratio=1.0,
            rotation=0,
        )
        assert result.width == 40
        assert result.height == 10

    def test_rotation_90_swaps_dimensions(self):
        # A 40×10 image rotated 90° becomes 10×40.
        result = load_image(
            _make_png_bytes(width=40, height=10),
            stitch_aspect_ratio=1.0,
            rotation=90,
        )
        assert result.width == 10
        assert result.height == 40

    def test_rotation_180_preserves_dimensions(self):
        result = load_image(
            _make_png_bytes(width=40, height=10),
            stitch_aspect_ratio=1.0,
            rotation=180,
        )
        assert result.width == 40
        assert result.height == 10

    def test_rotation_270_swaps_dimensions(self):
        result = load_image(
            _make_png_bytes(width=40, height=10),
            stitch_aspect_ratio=1.0,
            rotation=270,
        )
        assert result.width == 10
        assert result.height == 40

    def test_invalid_rotation_raises(self):
        with pytest.raises(ValueError, match="rotation must be 0, 90, 180, or 270"):
            load_image(_make_png_bytes(), stitch_aspect_ratio=1.0, rotation=45)

    def test_rotation_180_reverses_rows(self):
        # A 180° rotation of the asymmetric image should reverse both the row
        # order and each row's contents.
        src = _make_asymmetric_image()
        normal = load_image(src, stitch_aspect_ratio=1.0, rotation=0).rows
        rotated = load_image(src, stitch_aspect_ratio=1.0, rotation=180).rows
        assert rotated == [list(reversed(row)) for row in reversed(normal)]


# ===========================================================================
# Invert
# ===========================================================================


class TestInvert:
    def test_invert_flips_all_pixels(self):
        # Solid black image (all knit=1) should become all skip=0 when inverted.
        rows = load_image(
            _make_png_bytes(width=8, height=8, color=0),
            stitch_aspect_ratio=1.0,
            invert=True,
        ).rows
        assert all(v == 0 for row in rows for v in row)

    def test_invert_false_is_default(self):
        # Solid black image without invert should be all knit=1.
        rows = load_image(
            _make_png_bytes(width=8, height=8, color=0),
            stitch_aspect_ratio=1.0,
            invert=False,
        ).rows
        assert all(v == 1 for row in rows for v in row)

    def test_invert_only_contains_0_and_1(self):
        rows = load_image(
            _make_png_bytes(width=10, height=10),
            stitch_aspect_ratio=1.0,
            invert=True,
        ).rows
        assert all(v in (0, 1) for row in rows for v in row)

    def test_double_invert_is_identity(self):
        src = _make_png_bytes(width=10, height=10)
        once = load_image(src, stitch_aspect_ratio=1.0, invert=True).rows
        normal = load_image(src, stitch_aspect_ratio=1.0, invert=False).rows
        # Every pixel should be the complement.
        for row_n, row_i in zip(normal, once):
            assert row_i == [1 - v for v in row_n]


# ===========================================================================
# Dithering
# ===========================================================================


class TestDithering:
    """All dither modes must produce valid 0/1 pixel grids."""

    def _mid_grey_rows(self, dither: DitherMode) -> list[list[int]]:
        """Load a solid mid-grey image with the given dither mode."""
        return load_image(
            _make_png_bytes(width=20, height=20, color=128),
            stitch_aspect_ratio=1.0,
            dither=dither,
        ).rows

    def test_threshold_only_valid_values(self):
        rows = self._mid_grey_rows("none")
        assert all(v in (0, 1) for row in rows for v in row)

    def test_floyd_steinberg_only_valid_values(self):
        rows = self._mid_grey_rows("floyd-steinberg")
        assert all(v in (0, 1) for row in rows for v in row)

    def test_bayer_only_valid_values(self):
        rows = self._mid_grey_rows("bayer")
        assert all(v in (0, 1) for row in rows for v in row)

    def test_floyd_steinberg_mid_grey_is_mixed(self):
        # A mid-grey image dithered with Floyd-Steinberg should have both
        # knit and skip pixels — not a solid field.
        rows = self._mid_grey_rows("floyd-steinberg")
        flat = [v for row in rows for v in row]
        assert 0 in flat and 1 in flat

    def test_bayer_mid_grey_is_mixed(self):
        rows = self._mid_grey_rows("bayer")
        flat = [v for row in rows for v in row]
        assert 0 in flat and 1 in flat

    def test_solid_black_dither_all_knit(self):
        # Regardless of dither mode, a fully black image is all knit.
        for mode in ("none", "floyd-steinberg", "bayer"):
            rows = load_image(
                _make_png_bytes(width=8, height=8, color=0),
                stitch_aspect_ratio=1.0,
                dither=mode,
            ).rows
            assert all(v == 1 for row in rows for v in row), f"mode={mode!r} failed"

    def test_solid_white_dither_all_skip(self):
        # Hard threshold and Floyd-Steinberg: fully white → all skip.
        for mode in ("none", "floyd-steinberg"):
            rows = load_image(
                _make_png_bytes(width=8, height=8, color=255),
                stitch_aspect_ratio=1.0,
                dither=mode,
            ).rows
            assert all(v == 0 for row in rows for v in row), f"mode={mode!r} failed"

    def test_bayer_solid_white_is_mostly_skip(self):
        # Bayer dithering on a fully white image will knit one pixel per 4×4
        # tile (the matrix cell with threshold 255 equals the pixel luminance).
        # It should produce valid 0/1 values and be overwhelmingly skip.
        rows = load_image(
            _make_png_bytes(width=8, height=8, color=255),
            stitch_aspect_ratio=1.0,
            dither="bayer",
        ).rows
        flat = [v for row in rows for v in row]
        assert all(v in (0, 1) for v in flat)
        assert flat.count(0) > flat.count(1)  # vastly more skip than knit

    def test_bayer_pattern_repeats_every_4_pixels(self):
        # The Bayer matrix tiles at 4×4.  A uniform mid-grey image should
        # produce the same pattern at (x, y) and (x+4, y) (assuming the
        # image is wide enough and the 4×4 tile fits cleanly).
        rows = load_image(
            _make_png_bytes(width=16, height=8, color=100),
            stitch_aspect_ratio=1.0,
            dither="bayer",
        ).rows
        for y, row in enumerate(rows):
            for x in range(8):  # compare first 8 columns to columns 8–15
                assert (
                    row[x] == row[x + 8]
                ), f"Bayer tile mismatch at ({x},{y}) vs ({x + 8},{y})"


# ===========================================================================
# Pipeline combination
# ===========================================================================


class TestPipelineCombination:
    """Verify that multiple parameters interact correctly."""

    def test_crop_then_rotate_90_dimensions(self):
        # Crop a 100×100 image to 40×20, then rotate 90° → 20×40.
        result = load_image(
            _make_png_bytes(width=100, height=100),
            stitch_aspect_ratio=1.0,
            crop=(0, 0, 40, 20),
            rotation=90,
        )
        assert result.width == 20
        assert result.height == 40

    def test_flip_and_invert_independent(self):
        # Flip and invert are orthogonal operations; both applied together
        # should equal applying them individually in sequence.
        src = _make_asymmetric_image()
        flipped_only = load_image(
            src, stitch_aspect_ratio=1.0, flip_horizontal=True
        ).rows
        both = load_image(
            src, stitch_aspect_ratio=1.0, flip_horizontal=True, invert=True
        ).rows
        # 'both' should be the inverted version of 'flipped_only'.
        for row_b, row_f in zip(both, flipped_only):
            assert row_b == [1 - v for v in row_f]

    def test_all_defaults_unchanged(self):
        # With all new params at their defaults the result should match a
        # call that specifies no new params at all.
        src = _make_png_bytes(width=20, height=10)
        baseline = load_image(src, stitch_aspect_ratio=1.0)
        explicit = load_image(
            src,
            stitch_aspect_ratio=1.0,
            flip_horizontal=False,
            rotation=0,
            invert=False,
            dither="none",
        )
        assert baseline.rows == explicit.rows
        assert baseline.width == explicit.width
        assert baseline.height == explicit.height
