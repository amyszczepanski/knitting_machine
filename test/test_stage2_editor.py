"""
tests/test_stage2_editor.py — Tests for the Stage 2 pixel editor endpoints.

Covers:
  GET  /pattern/{number}/pixels — read committed pixel data and memo values
  PUT  /pattern/{number}        — overwrite an existing pattern (delete + rewrite)

Run with:
    pytest tests/test_stage2_editor.py -v

Import strategy
---------------
app.api is imported and patched at module level in test_api.py, and re-used
by test_new_api_endpoints.py.  We follow the same pattern here: import the
already-patched module and client from test_api rather than re-importing
app.api independently.  This ensures _state is the same singleton across all
test files, and avoids a second patch attempt that would conflict.

DiskImage and MachineModel are imported from the real app.brother_format
(which is never mocked) so that _reset_disk creates genuine disk objects.

Because app.api's DiskImage reference is _mock_disk_image_cls, any endpoint
that calls DiskImage.blank() internally (such as edit_pattern) will call the
mock.  TestPutPattern.setup_method therefore sets a side_effect on blank()
that delegates to the real DiskImage.blank(), ensuring edit_pattern builds a
genuine disk when it rebuilds after an edit.  teardown_method restores the
mock to its original return_value so other test classes are unaffected.
"""

from __future__ import annotations

# Re-use the already-patched module and client from test_api — same pattern
# as test_new_api_endpoints.py.
from .test_api import _api_module, _mock_disk_image_cls, client

from app.brother_format import DiskImage, MachineModel

_state = _api_module._state

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SMALL_PIXELS: list[list[int]] = [
    [1, 0, 1, 0],
    [0, 1, 0, 1],
    [1, 1, 0, 0],
]
_SMALL_MEMO: list[int] = [1, 2, 3]


def _reset_disk() -> None:
    """Replace _state.disk with a fresh real DiskImage before each test."""
    _state.disk = DiskImage.blank(MachineModel.KH940)


def _write_pattern(number: int, pixels: list[list[int]], memo: list[int]) -> None:
    """Write a pattern directly into _state.disk (bypasses HTTP layer)."""
    _state.disk.write_pattern(number, pixels, memo)


# ===========================================================================
# GET /pattern/{number}/pixels
# ===========================================================================


class TestGetPatternPixels:
    def setup_method(self) -> None:
        _reset_disk()

    def test_returns_pixels_and_memo(self) -> None:
        _write_pattern(901, _SMALL_PIXELS, _SMALL_MEMO)
        r = client.get("/pattern/901/pixels")
        assert r.status_code == 200
        data = r.json()
        assert data["number"] == 901
        assert data["pixels"] == _SMALL_PIXELS
        assert data["memo"] == _SMALL_MEMO

    def test_returns_correct_dimensions(self) -> None:
        _write_pattern(901, _SMALL_PIXELS, _SMALL_MEMO)
        r = client.get("/pattern/901/pixels")
        data = r.json()
        assert data["width"] == 4
        assert data["height"] == 3

    def test_404_for_missing_pattern(self) -> None:
        r = client.get("/pattern/901/pixels")
        assert r.status_code == 404

    def test_404_for_wrong_number(self) -> None:
        _write_pattern(901, _SMALL_PIXELS, _SMALL_MEMO)
        r = client.get("/pattern/902/pixels")
        assert r.status_code == 404

    def test_all_zero_memo_is_returned(self) -> None:
        _state.disk.write_pattern(901, _SMALL_PIXELS, None)
        r = client.get("/pattern/901/pixels")
        data = r.json()
        assert data["memo"] == [0, 0, 0]

    def test_wider_pattern(self) -> None:
        wide = [[i % 2 for i in range(20)] for _ in range(5)]
        memo = [0] * 5
        _write_pattern(901, wide, memo)
        r = client.get("/pattern/901/pixels")
        assert r.status_code == 200
        data = r.json()
        assert data["width"] == 20
        assert data["height"] == 5
        assert data["pixels"] == wide

    def test_pixels_contain_only_0_and_1(self) -> None:
        _write_pattern(901, _SMALL_PIXELS, _SMALL_MEMO)
        r = client.get("/pattern/901/pixels")
        data = r.json()
        assert all(v in (0, 1) for row in data["pixels"] for v in row)

    def test_memo_values_in_range(self) -> None:
        memo = [0, 7, 15]
        _write_pattern(901, _SMALL_PIXELS, memo)
        r = client.get("/pattern/901/pixels")
        data = r.json()
        assert all(0 <= v <= 15 for v in data["memo"])


# ===========================================================================
# PUT /pattern/{number}
# ===========================================================================


class TestPutPattern:
    def setup_method(self) -> None:
        _reset_disk()
        # edit_pattern calls DiskImage.blank() internally.  Because app.api's
        # DiskImage is the mock, we set a side_effect that delegates to the
        # real implementation so the rebuilt disk is a genuine DiskImage.
        _mock_disk_image_cls.blank.side_effect = (
            lambda model=MachineModel.KH940: DiskImage.blank(model)
        )

    def teardown_method(self) -> None:
        # Restore the mock to a neutral state so other test classes that rely
        # on return_value behaviour (e.g. TestDeletePattern) are unaffected.
        _mock_disk_image_cls.blank.side_effect = None

    def _put(self, number: int, pixels: list[list[int]], memo: list[int]):
        return client.put(
            f"/pattern/{number}",
            json={"pixels": pixels, "memo": memo},
        )

    def test_edit_single_pixel(self) -> None:
        _write_pattern(901, _SMALL_PIXELS, _SMALL_MEMO)
        edited = [row[:] for row in _SMALL_PIXELS]
        edited[0][0] = 1 - edited[0][0]
        r = self._put(901, edited, _SMALL_MEMO)
        assert r.status_code == 200
        r2 = client.get("/pattern/901/pixels")
        assert r2.json()["pixels"] == edited

    def test_edit_memo_values(self) -> None:
        _write_pattern(901, _SMALL_PIXELS, [0, 0, 0])
        new_memo = [5, 10, 15]
        self._put(901, _SMALL_PIXELS, new_memo)
        r = client.get("/pattern/901/pixels")
        assert r.json()["memo"] == new_memo

    def test_round_trip_preserves_other_patterns(self) -> None:
        pixels_b = [[0, 1], [1, 0]]
        _write_pattern(901, _SMALL_PIXELS, _SMALL_MEMO)
        _write_pattern(902, pixels_b, [0, 0])
        edited = [[0] * 4 for _ in range(3)]
        self._put(901, edited, [0, 0, 0])
        r902 = client.get("/pattern/902/pixels")
        assert r902.status_code == 200
        assert r902.json()["pixels"] == pixels_b

    def test_response_contains_correct_dimensions(self) -> None:
        _write_pattern(901, _SMALL_PIXELS, _SMALL_MEMO)
        edited = [[1, 0, 1, 0], [0, 1, 0, 1], [1, 0, 1, 0]]
        r = self._put(901, edited, [0, 0, 0])
        data = r.json()
        assert data["number"] == 901
        assert data["width"] == 4
        assert data["height"] == 3

    def test_404_for_nonexistent_pattern(self) -> None:
        r = self._put(901, _SMALL_PIXELS, _SMALL_MEMO)
        assert r.status_code == 404

    def test_422_for_empty_pixels(self) -> None:
        _write_pattern(901, _SMALL_PIXELS, _SMALL_MEMO)
        r = self._put(901, [], [])
        assert r.status_code == 422

    def test_422_for_unequal_row_widths(self) -> None:
        _write_pattern(901, _SMALL_PIXELS, _SMALL_MEMO)
        r = self._put(901, [[1, 0, 1], [0, 1]], [0, 0])
        assert r.status_code == 422

    def test_422_for_invalid_pixel_value(self) -> None:
        _write_pattern(901, _SMALL_PIXELS, _SMALL_MEMO)
        r = self._put(901, [[1, 2, 0, 1], [0, 1, 0, 1], [1, 0, 1, 0]], _SMALL_MEMO)
        assert r.status_code == 422

    def test_422_for_memo_value_too_high(self) -> None:
        _write_pattern(901, _SMALL_PIXELS, _SMALL_MEMO)
        r = self._put(901, _SMALL_PIXELS, [0, 16, 0])
        assert r.status_code == 422

    def test_422_for_memo_value_negative(self) -> None:
        _write_pattern(901, _SMALL_PIXELS, _SMALL_MEMO)
        r = self._put(901, _SMALL_PIXELS, [0, -1, 0])
        assert r.status_code == 422

    def test_edit_does_not_change_pattern_count(self) -> None:
        _write_pattern(901, _SMALL_PIXELS, _SMALL_MEMO)
        _write_pattern(902, [[1, 0], [0, 1]], [0, 0])
        self._put(901, [[0] * 4 for _ in range(3)], [0, 0, 0])
        r = client.get("/patterns")
        nums = [p["number"] for p in r.json()["patterns"]]
        assert sorted(nums) == [901, 902]

    def test_full_pixel_round_trip(self) -> None:
        """Write, edit via PUT, read back — confirm bit-exact round-trip."""
        original = [[1, 0, 1, 0, 1, 0, 1, 0] for _ in range(4)]
        original_memo = [1, 3, 5, 7]
        _write_pattern(901, original, original_memo)
        edited_pixels = [[1 - v for v in row] for row in original]
        edited_memo = [2, 4, 6, 8]
        self._put(901, edited_pixels, edited_memo)
        r = client.get("/pattern/901/pixels")
        data = r.json()
        assert data["pixels"] == edited_pixels
        assert data["memo"] == edited_memo
