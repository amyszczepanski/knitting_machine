"""
tests/test_new_api_endpoints.py — pytest unit tests for the endpoints added
in the Step 1–4 batch:

  GET  /disk/status
  GET  /disk/download
  POST /disk/upload
  GET  /preview/pattern/{number}
  DELETE /pattern/{number}

Run with:
    pytest tests/test_new_api_endpoints.py -v

Import strategy
---------------
app.api is already imported and patched in test_api.py.  We re-use that
module object and the TestClient from there to avoid double-importing the
FastAPI app (which would fight over the patched sys.modules state).

All tests manipulate _state.disk directly to set up the condition under
test, then restore it via setup_method / teardown_method so that tests
remain independent.
"""

from __future__ import annotations

import base64
import io
from unittest.mock import MagicMock

import pytest
from PIL import Image

# Re-use the already-patched module and client from test_api so we don't
# re-import app.api against the real (unpatched) brother_format.
from .test_api import _api_module, _mock_disk, _mock_disk_image_cls, client

_state = _api_module._state


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pixel_rows(stitches: int = 10, rows: int = 5) -> list[list[int]]:
    """Return a simple checkerboard pixel grid."""
    return [[(x + y) % 2 for x in range(stitches)] for y in range(rows)]


def _make_disk_with_patterns(*numbers: int) -> MagicMock:
    """
    Return a mock DiskImage that reports the given pattern numbers.

    list_patterns() returns PatternEntry-like mocks.
    read_pattern(n) returns a 5×10 pixel grid for any number in `numbers`.
    read_memo(n) returns a list of five zeros.
    get_pattern_entry(n) returns a mock entry or None.
    """
    entries = []
    for num in numbers:
        e = MagicMock()
        e.number = num
        e.stitches = 10
        e.rows = 5
        entries.append(e)

    disk = MagicMock()
    disk.max_rows = 500
    disk._init_pattern_offset = 0x7EDF
    disk._next_slot = len(numbers)
    disk._max_patterns = 98
    disk.bytes_remaining = 0x7EDF - len(numbers) * 20  # rough approximation
    disk.list_patterns.return_value = entries

    pixel_rows = _make_pixel_rows()

    def _read_pattern(n):
        if n in numbers:
            return pixel_rows
        raise KeyError(f"Pattern {n} not found")

    disk.read_pattern.side_effect = _read_pattern

    def _read_memo(n):
        if n in numbers:
            return [0] * 5
        raise KeyError(f"Pattern {n} not found")

    disk.read_memo.side_effect = _read_memo

    def _get_entry(n):
        return next((e for e in entries if e.number == n), None)

    disk.get_pattern_entry.side_effect = _get_entry

    return disk


# ---------------------------------------------------------------------------
# GET /disk/status
# ---------------------------------------------------------------------------


class TestDiskStatus:
    def setup_method(self):
        self._orig_disk = _state.disk
        disk = _make_disk_with_patterns(901, 902)
        disk.bytes_remaining = 0x7000
        disk._init_pattern_offset = 0x7EDF
        disk._next_slot = 2
        disk._max_patterns = 98
        _state.disk = disk

    def teardown_method(self):
        _state.disk = self._orig_disk

    def test_returns_200(self):
        resp = client.get("/disk/status")
        assert resp.status_code == 200

    def test_response_contains_patterns(self):
        resp = client.get("/disk/status")
        body = resp.json()
        numbers = [p["number"] for p in body["patterns"]]
        assert 901 in numbers
        assert 902 in numbers

    def test_response_contains_capacity_fields(self):
        resp = client.get("/disk/status")
        body = resp.json()
        for field in ("bytes_remaining", "bytes_total", "slots_used", "slots_total"):
            assert field in body, f"Missing field: {field}"

    def test_bytes_remaining_matches_disk(self):
        resp = client.get("/disk/status")
        body = resp.json()
        assert body["bytes_remaining"] == _state.disk.bytes_remaining

    def test_slots_used_matches_disk(self):
        resp = client.get("/disk/status")
        body = resp.json()
        assert body["slots_used"] == _state.disk._next_slot

    def test_slots_total_matches_disk(self):
        resp = client.get("/disk/status")
        body = resp.json()
        assert body["slots_total"] == _state.disk._max_patterns

    def test_pattern_dimensions_correct(self):
        resp = client.get("/disk/status")
        patterns = {p["number"]: p for p in resp.json()["patterns"]}
        assert patterns[901]["stitches"] == 10
        assert patterns[901]["rows"] == 5

    def test_empty_disk_returns_empty_pattern_list(self):
        disk = _make_disk_with_patterns()
        disk.bytes_remaining = 0x7EDF
        disk._init_pattern_offset = 0x7EDF
        disk._next_slot = 0
        disk._max_patterns = 98
        _state.disk = disk
        resp = client.get("/disk/status")
        assert resp.json()["patterns"] == []


# ---------------------------------------------------------------------------
# GET /disk/download
# ---------------------------------------------------------------------------


class TestDiskDownload:
    _BLOB_SIZE = 81_920  # 80 sectors × 1,024 bytes

    def setup_method(self):
        self._orig_disk = _state.disk
        disk = _make_disk_with_patterns(901)
        disk.to_disk_image_bytes.return_value = b"\xab" * self._BLOB_SIZE
        _state.disk = disk

    def teardown_method(self):
        _state.disk = self._orig_disk

    def test_returns_200(self):
        resp = client.get("/disk/download")
        assert resp.status_code == 200

    def test_content_type_is_octet_stream(self):
        resp = client.get("/disk/download")
        assert "application/octet-stream" in resp.headers["content-type"]

    def test_content_disposition_attachment(self):
        resp = client.get("/disk/download")
        cd = resp.headers.get("content-disposition", "")
        assert "attachment" in cd

    def test_content_disposition_filename(self):
        resp = client.get("/disk/download")
        cd = resp.headers.get("content-disposition", "")
        assert "knitting_disk.bin" in cd

    def test_body_is_blob_from_disk(self):
        resp = client.get("/disk/download")
        assert resp.content == b"\xab" * self._BLOB_SIZE

    def test_calls_to_disk_image_bytes(self):
        _state.disk.to_disk_image_bytes.reset_mock()
        client.get("/disk/download")
        _state.disk.to_disk_image_bytes.assert_called_once()


# ---------------------------------------------------------------------------
# POST /disk/upload
# ---------------------------------------------------------------------------


class TestDiskUpload:
    # Minimal valid blob: just needs to be long enough for from_bytes().
    # We mock from_bytes so any non-empty bytes will do; we use 81,920 to
    # represent a realistic full disk image.
    _BLOB = b"\x55" * 81_920

    def setup_method(self):
        self._orig_disk = _state.disk

    def teardown_method(self):
        _state.disk = self._orig_disk
        # Restore the class-level mock to a clean state.
        _mock_disk_image_cls.from_bytes.reset_mock()
        _mock_disk_image_cls.from_bytes.side_effect = None

    def _upload(self, blob: bytes = _BLOB, force: bool | None = None) -> object:
        data = {}
        if force is not None:
            data["force"] = "true" if force else "false"
        return client.post(
            "/disk/upload",
            data=data,
            files={"file": ("knitting_disk.bin", blob, "application/octet-stream")},
        )

    def _make_restored_disk(self, *pattern_numbers: int) -> MagicMock:
        """Return a mock disk that from_bytes() will return."""
        disk = _make_disk_with_patterns(*pattern_numbers)
        disk.bytes_remaining = 0x7EDF
        disk._init_pattern_offset = 0x7EDF
        disk._next_slot = len(pattern_numbers)
        disk._max_patterns = 98
        return disk

    # ---- empty RAM disk (no guard needed) ----------------------------------

    def test_upload_to_empty_disk_returns_200(self):
        _state.disk = _make_disk_with_patterns()  # empty
        restored = self._make_restored_disk(901, 902)
        _mock_disk_image_cls.from_bytes.return_value = restored
        resp = self._upload()
        assert resp.status_code == 200

    def test_upload_to_empty_disk_returns_pattern_count(self):
        _state.disk = _make_disk_with_patterns()
        restored = self._make_restored_disk(901, 902)
        _mock_disk_image_cls.from_bytes.return_value = restored
        resp = self._upload()
        assert resp.json()["patterns_restored"] == 2

    def test_upload_replaces_state_disk(self):
        _state.disk = _make_disk_with_patterns()
        restored = self._make_restored_disk(901)
        _mock_disk_image_cls.from_bytes.return_value = restored
        self._upload()
        assert _state.disk is restored

    def test_upload_calls_from_bytes(self):
        _state.disk = _make_disk_with_patterns()
        restored = self._make_restored_disk(901)
        _mock_disk_image_cls.from_bytes.return_value = restored
        self._upload(blob=self._BLOB)
        _mock_disk_image_cls.from_bytes.assert_called_once()
        call_args = _mock_disk_image_cls.from_bytes.call_args
        assert call_args[0][0] == self._BLOB  # first positional arg is the blob

    # ---- guard: non-empty RAM disk without force -------------------------

    def test_upload_to_nonempty_disk_without_force_returns_409(self):
        _state.disk = _make_disk_with_patterns(901)
        resp = self._upload(force=False)
        assert resp.status_code == 409

    def test_409_body_contains_pattern_count(self):
        _state.disk = _make_disk_with_patterns(901, 902)
        resp = self._upload()
        detail = resp.json()["detail"]
        # detail is a dict when the disk is non-empty
        assert detail["pattern_count"] == 2

    def test_409_does_not_replace_disk(self):
        original_disk = _make_disk_with_patterns(901)
        _state.disk = original_disk
        self._upload()
        assert _state.disk is original_disk

    # ---- force=true overrides the guard ----------------------------------

    def test_upload_with_force_to_nonempty_disk_returns_200(self):
        _state.disk = _make_disk_with_patterns(901)
        restored = self._make_restored_disk(902)
        _mock_disk_image_cls.from_bytes.return_value = restored
        resp = self._upload(force=True)
        assert resp.status_code == 200

    def test_upload_with_force_replaces_disk(self):
        _state.disk = _make_disk_with_patterns(901)
        restored = self._make_restored_disk(902)
        _mock_disk_image_cls.from_bytes.return_value = restored
        self._upload(force=True)
        assert _state.disk is restored

    # ---- error cases -------------------------------------------------------

    def test_empty_file_returns_400(self):
        _state.disk = _make_disk_with_patterns()
        resp = self._upload(blob=b"")
        assert resp.status_code == 400

    def test_unparseable_blob_returns_422(self):
        _state.disk = _make_disk_with_patterns()
        _mock_disk_image_cls.from_bytes.side_effect = ValueError("Data too short")
        resp = self._upload(blob=b"\x00" * 10)
        assert resp.status_code == 422

    def test_response_contains_bytes_remaining(self):
        _state.disk = _make_disk_with_patterns()
        restored = self._make_restored_disk(901)
        _mock_disk_image_cls.from_bytes.return_value = restored
        resp = self._upload()
        assert "bytes_remaining" in resp.json()


# ---------------------------------------------------------------------------
# GET /preview/pattern/{number}
# ---------------------------------------------------------------------------


class TestPreviewPattern:
    def setup_method(self):
        self._orig_disk = _state.disk
        _state.disk = _make_disk_with_patterns(901, 902)

    def teardown_method(self):
        _state.disk = self._orig_disk

    def test_existing_pattern_returns_200(self):
        resp = client.get("/preview/pattern/901")
        assert resp.status_code == 200

    def test_missing_pattern_returns_404(self):
        resp = client.get("/preview/pattern/999")
        assert resp.status_code == 404

    def test_response_has_data_uri(self):
        resp = client.get("/preview/pattern/901")
        assert resp.json()["data_uri"].startswith("data:image/png;base64,")

    def test_response_dimensions_match_pixel_rows(self):
        resp = client.get("/preview/pattern/901")
        body = resp.json()
        # _make_disk_with_patterns returns 10×5 pixel rows
        assert body["width"] == 10
        assert body["height"] == 5

    def test_data_uri_decodes_to_valid_png(self):
        resp = client.get("/preview/pattern/901")
        uri = resp.json()["data_uri"]
        b64_part = uri.split(",", 1)[1]
        png_bytes = base64.b64decode(b64_part)
        img = Image.open(io.BytesIO(png_bytes))
        assert img.format == "PNG"

    def test_png_dimensions_match_pixel_rows(self):
        resp = client.get("/preview/pattern/901")
        uri = resp.json()["data_uri"]
        b64_part = uri.split(",", 1)[1]
        png_bytes = base64.b64decode(b64_part)
        img = Image.open(io.BytesIO(png_bytes))
        assert img.size == (10, 5)

    def test_read_pattern_called_with_correct_number(self):
        _state.disk.read_pattern.reset_mock()
        client.get("/preview/pattern/902")
        _state.disk.read_pattern.assert_called_with(902)

    @pytest.mark.filterwarnings("ignore::DeprecationWarning")
    def test_knit_pixels_render_black(self):
        """Stitch value 1 should appear as pixel luminance 0 (black)."""
        solid_knit_disk = _make_disk_with_patterns(901)
        solid_knit_disk.read_pattern.side_effect = None
        solid_knit_disk.read_pattern.return_value = [[1, 1], [1, 1]]
        _state.disk = solid_knit_disk

        resp = client.get("/preview/pattern/901")
        b64_part = resp.json()["data_uri"].split(",", 1)[1]
        img = Image.open(io.BytesIO(base64.b64decode(b64_part))).convert("L")
        pixels = list(img.getdata())
        assert all(p == 0 for p in pixels), f"Expected all black, got: {pixels[:8]}"

    @pytest.mark.filterwarnings("ignore::DeprecationWarning")
    def test_skip_pixels_render_white(self):
        """Stitch value 0 should appear as pixel luminance 255 (white)."""
        solid_skip_disk = _make_disk_with_patterns(901)
        solid_skip_disk.read_pattern.side_effect = None
        solid_skip_disk.read_pattern.return_value = [[0, 0], [0, 0]]
        _state.disk = solid_skip_disk

        resp = client.get("/preview/pattern/901")
        b64_part = resp.json()["data_uri"].split(",", 1)[1]
        img = Image.open(io.BytesIO(base64.b64decode(b64_part))).convert("L")
        pixels = list(img.getdata())
        assert all(p == 255 for p in pixels), f"Expected all white, got: {pixels[:8]}"


# ---------------------------------------------------------------------------
# DELETE /pattern/{number}
# ---------------------------------------------------------------------------


class TestDeletePattern:
    def setup_method(self):
        self._orig_disk = _state.disk

    def teardown_method(self):
        _state.disk = self._orig_disk
        _mock_disk_image_cls.blank.reset_mock()
        _mock_disk_image_cls.blank.return_value = _mock_disk

    def _setup_disk(self, *numbers: int) -> MagicMock:
        disk = _make_disk_with_patterns(*numbers)
        _state.disk = disk
        return disk

    def test_delete_existing_pattern_returns_200(self):
        self._setup_disk(901)
        # blank() will be called during compaction; wire it up
        _mock_disk_image_cls.blank.return_value = _make_disk_with_patterns()
        resp = client.delete("/pattern/901")
        assert resp.status_code == 200

    def test_delete_missing_pattern_returns_404(self):
        self._setup_disk(901)
        resp = client.delete("/pattern/999")
        assert resp.status_code == 404

    def test_response_contains_deleted_number(self):
        self._setup_disk(901)
        _mock_disk_image_cls.blank.return_value = _make_disk_with_patterns()
        resp = client.delete("/pattern/901")
        assert resp.json()["deleted"] == 901

    def test_response_contains_patterns_remaining(self):
        self._setup_disk(901, 902)
        new_disk = _make_disk_with_patterns(902)
        _mock_disk_image_cls.blank.return_value = new_disk
        resp = client.delete("/pattern/901")
        assert resp.json()["patterns_remaining"] == 1

    def test_response_contains_bytes_remaining(self):
        self._setup_disk(901)
        new_disk = _make_disk_with_patterns()
        new_disk.bytes_remaining = 0x7EDF
        _mock_disk_image_cls.blank.return_value = new_disk
        resp = client.delete("/pattern/901")
        assert "bytes_remaining" in resp.json()

    def test_compaction_replaces_state_disk(self):
        self._setup_disk(901, 902)
        new_disk = _make_disk_with_patterns(902)
        _mock_disk_image_cls.blank.return_value = new_disk
        client.delete("/pattern/901")
        # After deletion, _state.disk should be the newly compacted disk
        assert _state.disk is new_disk

    def test_compaction_writes_survivors(self):
        """The surviving pattern should be re-written into the new disk."""
        self._setup_disk(901, 902)
        new_disk = _make_disk_with_patterns()
        new_disk.write_pattern.return_value = None
        _mock_disk_image_cls.blank.return_value = new_disk
        client.delete("/pattern/901")
        # write_pattern should have been called once (for survivor 902)
        new_disk.write_pattern.assert_called_once()
        call_args = new_disk.write_pattern.call_args
        assert call_args[0][0] == 902

    def test_compaction_preserves_memo(self):
        """Memo values for surviving patterns must be passed to write_pattern."""
        disk = self._setup_disk(901, 902)
        # Give pattern 902 a distinctive memo
        memo_902 = [1, 2, 3, 4, 5]
        disk.read_memo.side_effect = lambda n: memo_902 if n == 902 else [0] * 5
        new_disk = _make_disk_with_patterns()
        new_disk.write_pattern.return_value = None
        _mock_disk_image_cls.blank.return_value = new_disk
        client.delete("/pattern/901")
        call_args = new_disk.write_pattern.call_args
        # Third positional arg is memo_values
        assert call_args[0][2] == memo_902

    def test_delete_only_pattern_leaves_empty_disk(self):
        self._setup_disk(901)
        new_disk = _make_disk_with_patterns()
        _mock_disk_image_cls.blank.return_value = new_disk
        resp = client.delete("/pattern/901")
        assert resp.json()["patterns_remaining"] == 0

    def test_blank_called_with_model(self):
        """Compaction must create the new disk with the current machine model."""
        self._setup_disk(901)
        _mock_disk_image_cls.blank.return_value = _make_disk_with_patterns()
        client.delete("/pattern/901")
        _mock_disk_image_cls.blank.assert_called_once_with(_state.model)


# ---------------------------------------------------------------------------
# GET /disk/status — bytes_total derived from _init_pattern_offset
# ---------------------------------------------------------------------------


class TestDiskStatusBytesTotal:
    """bytes_total in the response should equal _init_pattern_offset, which
    is the total number of bytes available when the disk is empty."""

    def setup_method(self):
        self._orig_disk = _state.disk

    def teardown_method(self):
        _state.disk = self._orig_disk

    def test_bytes_total_matches_init_pattern_offset(self):
        disk = _make_disk_with_patterns(901)
        disk._init_pattern_offset = 0x7EDF
        disk.bytes_remaining = 0x7EDF - 100
        _state.disk = disk
        resp = client.get("/disk/status")
        assert resp.json()["bytes_total"] == 0x7EDF

    def test_used_bytes_is_total_minus_remaining(self):
        disk = _make_disk_with_patterns(901)
        disk._init_pattern_offset = 0x7EDF
        disk.bytes_remaining = 0x7EDF - 512
        _state.disk = disk
        resp = client.get("/disk/status")
        body = resp.json()
        used = body["bytes_total"] - body["bytes_remaining"]
        assert used == 512
