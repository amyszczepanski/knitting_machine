"""
tests/test_image.py — pytest unit tests for app/image.py.

Run with:
    pytest tests/test_image.py -v
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

# ---------------------------------------------------------------------------
# Patch sys.modules before any app.* imports.
#
# app.image itself doesn't use serial or brother_format, but if it shares an
# import path with app.api (e.g. via a package __init__), those heavy modules
# could be pulled in transitively.  The patch costs nothing and keeps the
# module-level import safe in all project layouts.
# ---------------------------------------------------------------------------

_mock_disk_image_cls = MagicMock()
_mock_machine_model = MagicMock()
_mock_machine_model.KH940 = "KH940"

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
    from app.image import ImageError, ImageResult, load_image  # noqa: E402

from .helpers import _make_png_bytes, _make_rgb_png_bytes  # noqa: E402

# ===========================================================================
# image.py tests
# ===========================================================================


class TestLoadImageInputTypes:
    """load_image accepts bytes, Path, and PIL Image objects."""

    def test_accepts_bytes(self):
        result = load_image(_make_png_bytes(), stitch_aspect_ratio=1.0)
        assert isinstance(result, ImageResult)

    def test_accepts_pil_image(self):
        img = Image.new("L", (20, 20), 200)
        result = load_image(img, stitch_aspect_ratio=1.0)
        assert isinstance(result, ImageResult)

    def test_accepts_path(self, tmp_path):
        p = tmp_path / "test.png"
        p.write_bytes(_make_png_bytes())
        result = load_image(p, stitch_aspect_ratio=1.0)
        assert isinstance(result, ImageResult)

    def test_bad_bytes_raises_image_error(self):
        with pytest.raises(ImageError, match="Cannot identify image format"):
            load_image(b"not an image")

    def test_missing_path_raises_image_error(self, tmp_path):
        with pytest.raises(ImageError, match="File not found"):
            load_image(tmp_path / "no_such_file.png")


class TestLoadImageWidthScaling:
    """Images wider than max_width are scaled down proportionally."""

    def test_wide_image_clamped_to_max_width(self):
        result = load_image(
            _make_png_bytes(width=400, height=100),
            max_width=200,
            stitch_aspect_ratio=1.0,
        )
        assert result.width == 200

    def test_narrow_image_unchanged(self):
        result = load_image(
            _make_png_bytes(width=50, height=50),
            max_width=200,
            stitch_aspect_ratio=1.0,
        )
        assert result.width == 50

    def test_exact_max_width_unchanged(self):
        result = load_image(
            _make_png_bytes(width=200, height=50),
            max_width=200,
            stitch_aspect_ratio=1.0,
        )
        assert result.width == 200

    def test_aspect_ratio_preserved_on_width_scale(self):
        # 400 × 200 scaled to max_width=100 → should be 100 × 50
        result = load_image(
            _make_png_bytes(width=400, height=200),
            max_width=100,
            stitch_aspect_ratio=1.0,
        )
        assert result.width == 100
        assert result.height == 50

    def test_orig_dimensions_preserved(self):
        result = load_image(
            _make_png_bytes(width=300, height=150),
            max_width=200,
            stitch_aspect_ratio=1.0,
        )
        assert result.orig_width == 300
        assert result.orig_height == 150


class TestStitchAspectRatio:
    """Vertical stretch is applied correctly after width-scaling."""

    def test_height_stretched_by_ratio(self):
        # 50 × 30 image, no width scaling needed, ratio=2.0 → height=60
        result = load_image(
            _make_png_bytes(width=50, height=30),
            max_width=200,
            stitch_aspect_ratio=2.0,
        )
        assert result.height == 60

    def test_ratio_1_no_change(self):
        result = load_image(
            _make_png_bytes(width=50, height=30),
            stitch_aspect_ratio=1.0,
        )
        assert result.height == 30

    def test_negative_ratio_raises(self):
        with pytest.raises(ValueError, match="stitch_aspect_ratio must be positive"):
            load_image(_make_png_bytes(), stitch_aspect_ratio=-1.0)

    def test_zero_ratio_raises(self):
        with pytest.raises(ValueError, match="stitch_aspect_ratio must be positive"):
            load_image(_make_png_bytes(), stitch_aspect_ratio=0.0)


class TestMaxRows:
    """max_rows clamps image height after aspect-ratio stretch."""

    def test_tall_image_clamped_to_max_rows(self):
        result = load_image(
            _make_png_bytes(width=10, height=500),
            stitch_aspect_ratio=1.0,
            max_rows=100,
        )
        assert result.height == 100

    def test_short_image_not_clamped(self):
        result = load_image(
            _make_png_bytes(width=10, height=50),
            stitch_aspect_ratio=1.0,
            max_rows=200,
        )
        assert result.height == 50

    def test_invalid_max_rows_raises(self):
        with pytest.raises(ValueError, match="max_rows must be a positive integer"):
            load_image(_make_png_bytes(), stitch_aspect_ratio=1.0, max_rows=0)

    def test_max_rows_applied_after_aspect_stretch(self):
        # 10 × 50 → after ratio=2.0 stretch → 100 rows → clamped to 80
        result = load_image(
            _make_png_bytes(width=10, height=50),
            stitch_aspect_ratio=2.0,
            max_rows=80,
        )
        assert result.height == 80


class TestBinarisation:
    """Threshold controls knit (1) vs skip (0) pixel mapping."""

    def _solid_image_result(self, lum: int, threshold: int) -> list[list[int]]:
        result = load_image(
            _make_png_bytes(width=5, height=5, color=lum),
            threshold=threshold,
            stitch_aspect_ratio=1.0,
        )
        return result.rows

    def test_below_threshold_is_knit(self):
        rows = self._solid_image_result(lum=50, threshold=128)
        assert all(v == 1 for row in rows for v in row)

    def test_above_threshold_is_skip(self):
        rows = self._solid_image_result(lum=200, threshold=128)
        assert all(v == 0 for row in rows for v in row)

    def test_equal_to_threshold_is_knit(self):
        rows = self._solid_image_result(lum=128, threshold=128)
        assert all(v == 1 for row in rows for v in row)

    def test_row_length_equals_width(self):
        result = load_image(
            _make_png_bytes(width=17, height=8),
            stitch_aspect_ratio=1.0,
        )
        assert all(len(row) == result.width for row in result.rows)

    def test_row_count_equals_height(self):
        result = load_image(
            _make_png_bytes(width=10, height=23),
            stitch_aspect_ratio=1.0,
        )
        assert len(result.rows) == result.height

    def test_rgb_image_converted_correctly(self):
        """Non-greyscale input should still produce 0/1 values."""
        result = load_image(
            _make_rgb_png_bytes(width=10, height=10),
            stitch_aspect_ratio=1.0,
        )
        assert all(v in (0, 1) for row in result.rows for v in row)


class TestCrop:
    """The crop parameter trims the image before scaling."""

    def test_valid_crop_reduces_orig_size(self):
        result = load_image(
            _make_png_bytes(width=100, height=100),
            crop=(10, 10, 60, 60),
            stitch_aspect_ratio=1.0,
        )
        # After crop the working area is 50×50; orig dims still reflect the
        # full image.
        assert result.orig_width == 100
        assert result.orig_height == 100
        assert result.width == 50
        assert result.height == 50

    def test_empty_crop_raises(self):
        with pytest.raises(ImageError, match="empty region"):
            load_image(
                _make_png_bytes(width=100, height=100),
                crop=(50, 50, 50, 50),  # zero-width box
                stitch_aspect_ratio=1.0,
            )

    def test_crop_clamped_to_image_bounds(self):
        # Crop extends beyond the image — should clamp rather than raise
        result = load_image(
            _make_png_bytes(width=50, height=50),
            crop=(0, 0, 999, 999),
            stitch_aspect_ratio=1.0,
        )
        assert result.width == 50
        assert result.height == 50
