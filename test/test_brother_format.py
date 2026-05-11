"""
test_brother_format.py — Tests for the Brother KH-940 disk image encoder.

Run with:
    pytest test_brother_format.py -v

Each test class targets a specific layer of the encoding stack, from the
lowest-level nibble primitives up to the full DiskImage round-trip and
metadata field values.  Failures in an early class will likely cause
failures in later classes too, so fix from the top down.
"""

import struct
import pytest

from app.brother_format import (
    # Geometry helpers
    nibbles_per_row,
    bytes_per_pattern,
    bytes_for_memo,
    # Low-level encode/decode
    encode_row,
    decode_row,
    encode_pattern_data,
    decode_pattern_data,
    encode_memo,
    decode_memo,
    # Directory entry encode/decode
    encode_directory_entry_940,
    decode_directory_entry_940,
    _encode_finhdr_940,
    # Top-level DiskImage
    DiskImage,
    MachineModel,
    # Constants
    KH940_REVERSED_BASE,
    KH940_CONTROL_DATA_ADDR,
    KH940_LOADED_PATTERN_ADDR,
    KH940_WORKING_REGION_SIZE,
    DIRECTORY_ENTRY_SIZE,
    SECTOR_SIZE,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def read_u16_be(data: bytes | bytearray, addr: int) -> int:
    """Read a big-endian unsigned 16-bit integer at file address `addr`."""
    return struct.unpack_from(">H", data, addr)[0]


def reversed_offset(file_addr: int) -> int:
    """Convert a file address to a KH-940 reversed-address offset."""
    return KH940_REVERSED_BASE - file_addr


def make_checkerboard(stitches: int, rows: int) -> list[list[int]]:
    """Return a checkerboard pixel grid (alternating 0/1)."""
    return [[(i + j) % 2 for j in range(stitches)] for i in range(rows)]


def make_solid(value: int, stitches: int, rows: int) -> list[list[int]]:
    """Return a solid grid of `value` (0 or 1)."""
    return [[value] * stitches for _ in range(rows)]


# ---------------------------------------------------------------------------
# 1. Geometry helpers
# ---------------------------------------------------------------------------


class TestGeometry:
    def test_nibbles_per_row_exact_multiple(self):
        assert nibbles_per_row(4) == 1
        assert nibbles_per_row(8) == 2
        assert nibbles_per_row(200) == 50

    def test_nibbles_per_row_rounds_up(self):
        assert nibbles_per_row(1) == 1
        assert nibbles_per_row(5) == 2
        assert nibbles_per_row(9) == 3

    def test_bytes_per_pattern_even_nibbles(self):
        # 4 stitches × 2 rows = 2 nibbles → 1 byte
        assert bytes_per_pattern(4, 2) == 1

    def test_bytes_per_pattern_odd_nibbles_rounds_up(self):
        # 4 stitches × 1 row = 1 nibble → rounds up to 1 byte
        assert bytes_per_pattern(4, 1) == 1
        # 4 stitches × 3 rows = 3 nibbles → rounds up to 2 bytes
        assert bytes_per_pattern(4, 3) == 2

    def test_bytes_for_memo_even_rows(self):
        assert bytes_for_memo(2) == 1
        assert bytes_for_memo(4) == 2

    def test_bytes_for_memo_odd_rows_rounds_up(self):
        assert bytes_for_memo(1) == 1
        assert bytes_for_memo(3) == 2


# ---------------------------------------------------------------------------
# 2. Row encode / decode round-trip
# ---------------------------------------------------------------------------


class TestRowEncoding:
    def test_all_zeros(self):
        row = [0, 0, 0, 0]
        assert decode_row(encode_row(row, 4), 4) == row

    def test_all_ones(self):
        row = [1, 1, 1, 1]
        assert decode_row(encode_row(row, 4), 4) == row

    def test_alternating(self):
        row = [1, 0, 1, 0]
        assert decode_row(encode_row(row, 4), 4) == row

    def test_non_multiple_of_4_stitches(self):
        # 5 stitches — must round up to 8 (2 nibbles) internally
        row = [1, 0, 1, 0, 1]
        assert decode_row(encode_row(row, 5), 5) == row

    def test_single_stitch(self):
        for v in [0, 1]:
            assert decode_row(encode_row([v], 1), 1) == [v]

    def test_bit_order_within_nibble(self):
        # stitch 0 = bit 0 (LSB), stitch 3 = bit 3 (MSB)
        # Only stitch 0 set → nibble = 0b0001 = 1
        nibbles = encode_row([1, 0, 0, 0], 4)
        assert nibbles[0] == 0b0001
        # Only stitch 3 set → nibble = 0b1000 = 8
        nibbles = encode_row([0, 0, 0, 1], 4)
        assert nibbles[0] == 0b1000

    def test_200_stitches(self):
        row = [i % 2 for i in range(200)]
        assert decode_row(encode_row(row, 200), 200) == row


# ---------------------------------------------------------------------------
# 3. Pattern data encode / decode round-trip
# ---------------------------------------------------------------------------


class TestPatternDataEncoding:
    def test_single_row_all_ones(self):
        rows = make_solid(1, 4, 1)
        encoded = encode_pattern_data(rows, 4, 1)
        decoded = decode_pattern_data(encoded, len(encoded) - 1, 4, 1)
        assert decoded == rows

    def test_checkerboard_4x4(self):
        rows = make_checkerboard(4, 4)
        encoded = encode_pattern_data(rows, 4, 4)
        decoded = decode_pattern_data(encoded, len(encoded) - 1, 4, 4)
        assert decoded == rows

    def test_non_multiple_stitches(self):
        rows = make_checkerboard(5, 3)
        encoded = encode_pattern_data(rows, 5, 3)
        decoded = decode_pattern_data(encoded, len(encoded) - 1, 5, 3)
        assert decoded == rows

    def test_large_pattern(self):
        rows = make_checkerboard(200, 100)
        encoded = encode_pattern_data(rows, 200, 100)
        decoded = decode_pattern_data(encoded, len(encoded) - 1, 200, 100)
        assert decoded == rows

    def test_encoded_length_matches_formula(self):
        for stitches in [1, 4, 5, 8, 200]:
            for row_count in [1, 2, 3, 10]:
                rows = make_solid(1, stitches, row_count)
                encoded = encode_pattern_data(rows, stitches, row_count)
                assert len(encoded) == bytes_per_pattern(stitches, row_count), (
                    f"stitches={stitches}, rows={row_count}: "
                    f"encoded {len(encoded)} bytes, expected {bytes_per_pattern(stitches, row_count)}"
                )

    def test_row_order_preserved(self):
        # Row 0 should come back as row 0, not reversed
        rows = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]
        encoded = encode_pattern_data(rows, 4, 4)
        decoded = decode_pattern_data(encoded, len(encoded) - 1, 4, 4)
        assert decoded == rows


# ---------------------------------------------------------------------------
# 4. Memo encode / decode round-trip
# ---------------------------------------------------------------------------


class TestMemoEncoding:
    def test_all_zeros(self):
        encoded = encode_memo(4, [0, 0, 0, 0])
        assert decode_memo(encoded, len(encoded) - 1, 4) == [0, 0, 0, 0]

    def test_mixed_values(self):
        values = [0, 5, 10, 15]
        encoded = encode_memo(4, values)
        assert decode_memo(encoded, len(encoded) - 1, 4) == values

    def test_defaults_to_zeros(self):
        encoded = encode_memo(4, None)
        assert decode_memo(encoded, len(encoded) - 1, 4) == [0, 0, 0, 0]

    def test_odd_row_count(self):
        values = [3, 7, 1]
        encoded = encode_memo(3, values)
        assert decode_memo(encoded, len(encoded) - 1, 3) == values

    def test_encoded_length_matches_formula(self):
        for row_count in [1, 2, 3, 10, 99]:
            encoded = encode_memo(row_count)
            assert len(encoded) == bytes_for_memo(row_count)


# ---------------------------------------------------------------------------
# 5. Directory entry encode / decode — KH-940
# ---------------------------------------------------------------------------


class TestDirectoryEntry940:
    def test_round_trip_basic(self):
        memo_offset = 0x7EDF  # initial pattern offset
        byte_offset, raw = encode_directory_entry_940(
            slot_index=0,
            number=901,
            stitches=40,
            rows=20,
            memo_offset=memo_offset,
        )
        assert byte_offset == 0
        entry = decode_directory_entry_940(raw)
        assert entry is not None
        assert entry.number == 901
        assert entry.stitches == 40
        assert entry.rows == 20

    def test_memo_offset_round_trips(self):
        memo_offset = 0x7EDF
        _, raw = encode_directory_entry_940(
            slot_index=0,
            number=901,
            stitches=40,
            rows=20,
            memo_offset=memo_offset,
        )
        entry = decode_directory_entry_940(raw)
        assert entry.memo_offset == memo_offset

    def test_slot_index_sets_byte_offset(self):
        for slot in range(5):
            byte_offset, _ = encode_directory_entry_940(
                slot_index=slot,
                number=901 + slot,
                stitches=4,
                rows=2,
                memo_offset=0x7EDF,
            )
            assert byte_offset == slot * DIRECTORY_ENTRY_SIZE

    def test_fill_byte_returns_none(self):
        raw = bytes([0x55] * DIRECTORY_ENTRY_SIZE)
        assert decode_directory_entry_940(raw) is None

    def test_pattern_number_bcd_extremes(self):
        for number in [901, 950, 999]:
            _, raw = encode_directory_entry_940(
                slot_index=0,
                number=number,
                stitches=4,
                rows=2,
                memo_offset=0x7EDF,
            )
            entry = decode_directory_entry_940(raw)
            assert entry.number == number

    def test_data_offset_field_is_reversed_address(self):
        memo_offset = 0x7EDF
        _, raw = encode_directory_entry_940(
            slot_index=0,
            number=901,
            stitches=4,
            rows=2,
            memo_offset=memo_offset,
        )
        # DATA_OFFSET = bytes 0–1 as big-endian 16-bit
        data_offset = (raw[0] << 8) | raw[1]
        assert data_offset == KH940_REVERSED_BASE - memo_offset


# ---------------------------------------------------------------------------
# 6. FINHDR sentinel
# ---------------------------------------------------------------------------


class TestFinhdr940:
    def test_bytes_0_to_4_are_fill(self):
        _, raw = _encode_finhdr_940(slot_index=1, next_number=902)
        assert raw[:5] == bytes([0x55] * 5)

    def test_next_number_encoded_in_bcd(self):
        _, raw = _encode_finhdr_940(slot_index=1, next_number=902)
        # next_number=902: hundreds=9, tens=0, ones=2
        # byte 5: 0x09  byte 6: 0x02
        assert raw[5] == 0x09, f"byte 5 = 0x{raw[5]:02X}, expected 0x09"
        assert raw[6] == 0x02, f"byte 6 = 0x{raw[6]:02X}, expected 0x02"

    def test_slot_index_sets_byte_offset(self):
        for slot in range(5):
            byte_offset, _ = _encode_finhdr_940(slot_index=slot, next_number=901)
            assert byte_offset == slot * DIRECTORY_ENTRY_SIZE

    def test_finhdr_decoded_as_none(self):
        _, raw = _encode_finhdr_940(slot_index=0, next_number=901)
        assert decode_directory_entry_940(raw) is None


# ---------------------------------------------------------------------------
# 7. DiskImage blank initialisation — KH-940 metadata fields
# ---------------------------------------------------------------------------


class TestBlankDiskImage940:
    @pytest.fixture
    def data(self) -> bytearray:
        return bytearray(DiskImage.blank(MachineModel.KH940).working_region_bytes())

    def test_last_byte_is_0x02(self, data):
        assert data[0x7FFF] == 0x02, f"LAST_BYTE = 0x{data[0x7FFF]:02X}, expected 0x02"

    def test_area0_is_0x55(self, data):
        for addr in range(0x7EE0, 0x7EE7):
            assert data[addr] == 0x55, f"AREA0[0x{addr:04X}] = 0x{data[addr]:02X}"

    def test_area1_is_0x00(self, data):
        for addr in range(0x7EE7, 0x7F00):
            assert data[addr] == 0x00, f"AREA1[0x{addr:04X}] = 0x{data[addr]:02X}"

    def test_area2_is_0x00(self, data):
        for addr in range(0x7F17, 0x7F30):
            assert data[addr] == 0x00, f"AREA2[0x{addr:04X}] = 0x{data[addr]:02X}"

    def test_area3_is_0x00(self, data):
        for addr in range(0x7F30, 0x7FEA):
            assert data[addr] == 0x00, f"AREA3[0x{addr:04X}] = 0x{data[addr]:02X}"

    def test_area4_is_0x00(self, data):
        for addr in range(0x7FEC, 0x7FFF):
            assert data[addr] == 0x00, f"AREA4[0x{addr:04X}] = 0x{data[addr]:02X}"

    def test_loaded_pattern_default(self, data):
        assert data[0x7FEA] == 0x10, f"LOADED_PATTERN[0] = 0x{data[0x7FEA]:02X}"
        assert data[0x7FEB] == 0x00, f"LOADED_PATTERN[1] = 0x{data[0x7FEB]:02X}"

    def test_control_unk1_is_0x0001(self, data):
        val = read_u16_be(data, KH940_CONTROL_DATA_ADDR + 0x02)
        assert val == 0x0001, f"UNK1 = 0x{val:04X}"

    def test_control_unk2_is_0x0000(self, data):
        val = read_u16_be(data, KH940_CONTROL_DATA_ADDR + 0x08)
        assert val == 0x0000, f"UNK2 = 0x{val:04X}"

    def test_control_header_ptr_after_format(self, data):
        val = read_u16_be(data, KH940_CONTROL_DATA_ADDR + 0x10)
        assert val == 0x7FF9, f"HEADER_PTR = 0x{val:04X}, expected 0x7FF9"

    def test_control_unk_ptr_is_0x0000(self, data):
        val = read_u16_be(data, KH940_CONTROL_DATA_ADDR + 0x12)
        assert val == 0x0000, f"UNK_PTR = 0x{val:04X}"

    def test_directory_area_is_0x55(self, data):
        # Unused directory slots should be filled with 0x55
        for addr in range(0x0000, 0x02AE):
            assert data[addr] == 0x55, f"DIR[0x{addr:04X}] = 0x{data[addr]:02X}"


# ---------------------------------------------------------------------------
# 8. DiskImage.write_pattern — single-pattern round-trip
# ---------------------------------------------------------------------------


class TestWritePatternRoundTrip:
    STITCHES = 40
    ROWS = 20
    NUMBER = 901

    @pytest.fixture
    def original_rows(self) -> list[list[int]]:
        return make_checkerboard(self.STITCHES, self.ROWS)

    @pytest.fixture
    def disk(self, original_rows) -> DiskImage:
        d = DiskImage.blank(MachineModel.KH940)
        d.write_pattern(self.NUMBER, original_rows)
        return d

    def test_list_patterns_shows_one_entry(self, disk):
        entries = disk.list_patterns()
        assert len(entries) == 1
        assert entries[0].number == self.NUMBER
        assert entries[0].stitches == self.STITCHES
        assert entries[0].rows == self.ROWS

    def test_read_pattern_matches_input(self, disk, original_rows):
        decoded = disk.read_pattern(self.NUMBER)
        assert decoded == original_rows

    def test_read_pattern_solid_ones(self):
        rows = make_solid(1, 8, 4)
        d = DiskImage.blank(MachineModel.KH940)
        d.write_pattern(901, rows)
        assert disk.read_pattern(901) if False else d.read_pattern(901) == rows

    def test_read_pattern_solid_zeros(self):
        rows = make_solid(0, 8, 4)
        d = DiskImage.blank(MachineModel.KH940)
        d.write_pattern(901, rows)
        assert d.read_pattern(901) == rows

    def test_pattern_entry_pointers_are_consistent(self, disk):
        entry = disk.get_pattern_entry(self.NUMBER)
        assert entry is not None
        pat_bytes = bytes_per_pattern(self.STITCHES, self.ROWS)
        memo_bytes = bytes_for_memo(self.ROWS)
        # pattern_offset should be exactly bytes_for_memo below memo_offset
        assert entry.pattern_offset == entry.memo_offset - memo_bytes
        # block_end_offset should be pat_bytes below pattern_offset
        assert entry.block_end_offset == entry.memo_offset - pat_bytes - memo_bytes


# ---------------------------------------------------------------------------
# 9. DiskImage metadata after write_pattern — KH-940 control block
# ---------------------------------------------------------------------------


class TestMetadataAfterWrite:
    STITCHES = 40
    ROWS = 20
    NUMBER = 901

    @pytest.fixture
    def disk_and_data(self) -> tuple[DiskImage, bytearray]:
        d = DiskImage.blank(MachineModel.KH940)
        rows = make_checkerboard(self.STITCHES, self.ROWS)
        d.write_pattern(self.NUMBER, rows)
        return d, bytearray(d.working_region_bytes())

    def test_last_byte_still_0x02(self, disk_and_data):
        _, data = disk_and_data
        assert data[0x7FFF] == 0x02

    def test_unk1_still_0x0001(self, disk_and_data):
        _, data = disk_and_data
        val = read_u16_be(data, KH940_CONTROL_DATA_ADDR + 0x02)
        assert val == 0x0001, f"UNK1 = 0x{val:04X}"

    def test_unk3_is_0x00008100(self, disk_and_data):
        _, data = disk_and_data
        base = KH940_CONTROL_DATA_ADDR
        unk3 = data[base + 0x0C : base + 0x10]
        assert unk3 == b"\x00\x00\x81\x00", f"UNK3 = {unk3.hex()}"

    def test_pattern_ptr1_equals_ptr0(self, disk_and_data):
        _, data = disk_and_data
        ptr1 = read_u16_be(data, KH940_CONTROL_DATA_ADDR + 0x00)
        ptr0 = read_u16_be(data, KH940_CONTROL_DATA_ADDR + 0x04)
        assert ptr1 == ptr0, f"PATTERN_PTR1=0x{ptr1:04X} != PATTERN_PTR0=0x{ptr0:04X}"

    def test_last_bottom_is_reversed_memo_offset(self, disk_and_data):
        disk, data = disk_and_data
        entry = disk.get_pattern_entry(self.NUMBER)
        expected = KH940_REVERSED_BASE - entry.memo_offset
        actual = read_u16_be(data, KH940_CONTROL_DATA_ADDR + 0x06)
        assert actual == expected, (
            f"LAST_BOTTOM=0x{actual:04X}, expected 0x{expected:04X} "
            f"(memo_offset=0x{entry.memo_offset:04X})"
        )

    def test_last_top_is_reversed_pattern_data_first_byte(self, disk_and_data):
        disk, data = disk_and_data
        entry = disk.get_pattern_entry(self.NUMBER)
        pat_bytes = bytes_per_pattern(self.STITCHES, self.ROWS)
        pat_first = entry.pattern_offset - pat_bytes + 1
        expected = KH940_REVERSED_BASE - pat_first
        actual = read_u16_be(data, KH940_CONTROL_DATA_ADDR + 0x0A)
        assert actual == expected, f"LAST_TOP=0x{actual:04X}, expected 0x{expected:04X}"

    def test_next_ptr_is_one_above_pattern_block(self, disk_and_data):
        disk, data = disk_and_data
        entry = disk.get_pattern_entry(self.NUMBER)
        pat_bytes = bytes_per_pattern(self.STITCHES, self.ROWS)
        pat_first = entry.pattern_offset - pat_bytes + 1
        expected = KH940_REVERSED_BASE - (pat_first - 1)
        actual = read_u16_be(data, KH940_CONTROL_DATA_ADDR + 0x00)
        assert (
            actual == expected
        ), f"PATTERN_PTR=0x{actual:04X}, expected 0x{expected:04X}"

    def test_header_ptr_points_to_finhdr(self, disk_and_data):
        disk, data = disk_and_data
        # After one pattern, FINHDR is at slot 1, file address = 1 × 7 = 7
        finhdr_file_addr = 1 * DIRECTORY_ENTRY_SIZE
        expected = KH940_REVERSED_BASE - finhdr_file_addr
        actual = read_u16_be(data, KH940_CONTROL_DATA_ADDR + 0x10)
        assert (
            actual == expected
        ), f"HEADER_PTR=0x{actual:04X}, expected 0x{expected:04X}"

    def test_loaded_pattern_matches_written_number(self, disk_and_data):
        _, data = disk_and_data
        # LOADED_PATTERN for 901: byte0 = 0x19 (0x1N where N=hundreds=9),
        # byte1 = 0x01 (tens=0, ones=1)
        assert (
            data[KH940_LOADED_PATTERN_ADDR] == 0x19
        ), f"LOADED_PATTERN[0] = 0x{data[KH940_LOADED_PATTERN_ADDR]:02X}, expected 0x19"
        assert (
            data[KH940_LOADED_PATTERN_ADDR + 1] == 0x01
        ), f"LOADED_PATTERN[1] = 0x{data[KH940_LOADED_PATTERN_ADDR+1]:02X}, expected 0x01"

    def test_finhdr_written_at_slot_1(self, disk_and_data):
        _, data = disk_and_data
        slot1_start = 1 * DIRECTORY_ENTRY_SIZE
        finhdr = data[slot1_start : slot1_start + DIRECTORY_ENTRY_SIZE]
        assert finhdr[:5] == bytes(
            [0x55] * 5
        ), f"FINHDR bytes 0–4 = {finhdr[:5].hex()}, expected 5555555555"


# ---------------------------------------------------------------------------
# 10. DiskImage — multiple patterns and serialisation
# ---------------------------------------------------------------------------


class TestMultiplePatterns:
    def test_two_patterns_both_round_trip(self):
        rows_a = make_checkerboard(4, 4)
        rows_b = make_solid(1, 8, 6)
        d = DiskImage.blank(MachineModel.KH940)
        d.write_pattern(901, rows_a)
        d.write_pattern(902, rows_b)
        assert d.read_pattern(901) == rows_a
        assert d.read_pattern(902) == rows_b

    def test_patterns_do_not_overlap(self):
        rows_a = make_solid(1, 4, 2)
        rows_b = make_solid(0, 4, 2)
        d = DiskImage.blank(MachineModel.KH940)
        d.write_pattern(901, rows_a)
        d.write_pattern(902, rows_b)
        # Writing 902 must not corrupt 901
        assert d.read_pattern(901) == rows_a

    def test_to_disk_image_bytes_correct_size(self):
        d = DiskImage.blank(MachineModel.KH940)
        d.write_pattern(901, make_checkerboard(4, 4))
        raw = d.to_disk_image_bytes()
        assert len(raw) == 80 * SECTOR_SIZE

    def test_to_disk_image_bytes_working_region_matches(self):
        d = DiskImage.blank(MachineModel.KH940)
        d.write_pattern(901, make_checkerboard(4, 4))
        raw = d.to_disk_image_bytes()
        working = d.working_region_bytes()
        assert raw[: len(working)] == working

    def test_from_bytes_round_trip(self):
        rows = make_checkerboard(40, 20)
        d = DiskImage.blank(MachineModel.KH940)
        d.write_pattern(901, rows)
        raw = d.to_disk_image_bytes()
        d2 = DiskImage.from_bytes(raw, MachineModel.KH940)
        assert d2.read_pattern(901) == rows

    def test_from_bytes_can_append_pattern(self):
        rows_a = make_checkerboard(4, 4)
        rows_b = make_solid(1, 4, 4)
        d = DiskImage.blank(MachineModel.KH940)
        d.write_pattern(901, rows_a)
        raw = d.to_disk_image_bytes()

        d2 = DiskImage.from_bytes(raw, MachineModel.KH940)
        d2.write_pattern(902, rows_b)
        assert d2.read_pattern(901) == rows_a
        assert d2.read_pattern(902) == rows_b

    def test_duplicate_pattern_number_raises(self):
        d = DiskImage.blank(MachineModel.KH940)
        d.write_pattern(901, make_solid(1, 4, 2))
        with pytest.raises(ValueError, match="already exists"):
            d.write_pattern(901, make_solid(0, 4, 2))
