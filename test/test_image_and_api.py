"""
tests/test_image_and_api.py — pytest unit tests for app/image.py and app/api.py.

Run with:
    pytest tests/test_image_and_api.py -v
"""

from __future__ import annotations

import base64
import io
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

# ---------------------------------------------------------------------------
# Helpers shared by both test modules
# ---------------------------------------------------------------------------


def _make_png_bytes(
    width: int = 10,
    height: int = 10,
    color: int = 128,
    mode: str = "L",
) -> bytes:
    """Return a minimal in-memory PNG as bytes."""
    img = Image.new(mode, (width, height), color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _make_rgb_png_bytes(width: int = 10, height: int = 10) -> bytes:
    return _make_png_bytes(width, height, color=(200, 100, 50), mode="RGB")


# ===========================================================================
# image.py tests
# ===========================================================================

from app.image import ImageError, ImageResult, load_image  # noqa: E402


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


# ===========================================================================
# api.py tests  (uses FastAPI TestClient)
# ===========================================================================

from fastapi.testclient import TestClient  # noqa: E402

# We need to mock the heavy dependencies before importing the app module so
# that tests don't require app.brother_format or app.serial_emulator to exist.

_mock_disk = MagicMock()
_mock_disk.max_rows = 500
_mock_disk.read_pattern.return_value = []  # no patterns by default
_mock_disk.write_pattern.return_value = None
_mock_disk.to_disk_image_bytes.return_value = b"\x00" * 16

_mock_disk_image_cls = MagicMock()
_mock_disk_image_cls.blank.return_value = _mock_disk

_mock_machine_model = MagicMock()
_mock_machine_model.KH940 = "KH940"

with patch.dict(
    "sys.modules",
    {
        "app.brother_format": MagicMock(
            DiskImage=_mock_disk_image_cls,
            MachineModel=_mock_machine_model,
        ),
        "app.serial_emulator": MagicMock(),
    },
):
    from app.api import app  # noqa: E402 — import after patching

client = TestClient(app)


class TestListPatterns:
    def test_empty_disk_returns_empty_list(self):
        _mock_disk.read_pattern.return_value = []
        resp = client.get("/patterns")
        assert resp.status_code == 200
        assert resp.json()["patterns"] == []

    def test_pattern_present_is_listed(self):
        # Simulate pattern 901 having 3 rows of 10 stitches
        def _read(number):
            if number == 901:
                return [[1] * 10] * 3
            return []

        _mock_disk.read_pattern.side_effect = _read
        resp = client.get("/patterns")
        assert resp.status_code == 200
        patterns = resp.json()["patterns"]
        assert any(p["number"] == 901 and p["rows"] == 3 for p in patterns)
        # Reset
        _mock_disk.read_pattern.side_effect = None
        _mock_disk.read_pattern.return_value = []


class TestWritePattern:
    def _upload(
        self, number: int = 901, threshold: int = 128, png: bytes | None = None
    ):
        png = png or _make_png_bytes()
        return client.post(
            "/pattern",
            data={"number": number, "threshold": threshold},
            files={"file": ("test.png", png, "image/png")},
        )

    def test_valid_upload_returns_200(self):
        resp = self._upload()
        assert resp.status_code == 200
        body = resp.json()
        assert body["number"] == 901
        assert body["width"] > 0
        assert body["height"] > 0

    def test_invalid_pattern_number_returns_422(self):
        resp = self._upload(number=800)  # out of 901–999 range
        assert resp.status_code == 422

    def test_empty_file_returns_400(self):
        resp = client.post(
            "/pattern",
            data={"number": 901},
            files={"file": ("empty.png", b"", "image/png")},
        )
        assert resp.status_code == 400

    def test_bad_image_bytes_returns_422(self):
        resp = client.post(
            "/pattern",
            data={"number": 901},
            files={"file": ("bad.png", b"not an image", "image/png")},
        )
        assert resp.status_code == 422


class TestPreview:
    def test_valid_image_returns_data_uri(self):
        resp = client.post(
            "/preview",
            data={"threshold": "128"},
            files={"file": ("test.png", _make_png_bytes(), "image/png")},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["data_uri"].startswith("data:image/png;base64,")
        assert body["width"] > 0
        assert body["height"] > 0

    def test_data_uri_is_valid_base64_png(self):
        resp = client.post(
            "/preview",
            data={},
            files={
                "file": ("test.png", _make_png_bytes(width=20, height=20), "image/png")
            },
        )
        assert resp.status_code == 200
        uri = resp.json()["data_uri"]
        b64 = uri.split(",", 1)[1]
        png_bytes = base64.b64decode(b64)
        img = Image.open(io.BytesIO(png_bytes))
        assert img.format == "PNG"

    def test_bad_image_returns_422(self):
        resp = client.post(
            "/preview",
            data={},
            files={"file": ("bad.png", b"garbage", "image/png")},
        )
        assert resp.status_code == 422

    def test_empty_file_returns_400(self):
        resp = client.post(
            "/preview",
            data={},
            files={"file": ("empty.png", b"", "image/png")},
        )
        assert resp.status_code == 400


class TestSendStatus:
    def setup_method(self):
        from app.api import _state

        _state.tasks.clear()

    def _post_send(self):
        with patch("app.api.threading.Thread") as mock_thread:
            mock_thread.return_value = MagicMock()
            return client.post("/send")

    def test_unknown_task_id_returns_404(self):
        resp = client.get("/send/00000000-0000-0000-0000-000000000000")
        assert resp.status_code == 404

    def test_send_returns_task_id(self):
        resp = self._post_send()
        assert resp.status_code == 200
        body = resp.json()
        assert "task_id" in body
        assert body["status"] in ("pending", "running", "done", "error")

    def test_send_status_reachable_after_send(self):
        send_resp = self._post_send()
        assert send_resp.status_code == 200, send_resp.json()
        task_id = send_resp.json()["task_id"]
        status_resp = client.get(f"/send/{task_id}")
        assert status_resp.status_code == 200
        assert status_resp.json()["task_id"] == task_id


class TestConfig:
    def test_get_config_returns_defaults(self):
        resp = client.get("/config")
        assert resp.status_code == 200
        body = resp.json()
        assert "serial_port" in body
        assert "baud_rate" in body
        assert "disk_dir" in body

    def test_put_config_updates_serial_port(self):
        resp = client.put("/config", json={"serial_port": "/dev/ttyUSB1"})
        assert resp.status_code == 200
        assert resp.json()["serial_port"] == "/dev/ttyUSB1"
        # Restore
        client.put("/config", json={"serial_port": "/dev/ttyUSB0"})

    def test_put_config_updates_baud_rate(self):
        resp = client.put("/config", json={"baud_rate": 19200})
        assert resp.status_code == 200
        assert resp.json()["baud_rate"] == 19200
        # Restore
        client.put("/config", json={"baud_rate": 9600})

    def test_put_config_partial_update_leaves_other_fields(self):
        # Only update disk_dir; serial_port should stay the same
        before = client.get("/config").json()["serial_port"]
        client.put("/config", json={"disk_dir": "/tmp/new_dir"})
        after = client.get("/config").json()["serial_port"]
        assert before == after
        # Restore
        client.put("/config", json={"disk_dir": "/tmp/knitting_disk"})


class TestResetDisk:
    def test_delete_disk_returns_ok(self):
        resp = client.delete("/disk")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_patterns_empty_after_reset(self):
        _mock_disk.read_pattern.return_value = []
        client.delete("/disk")
        resp = client.get("/patterns")
        assert resp.json()["patterns"] == []
