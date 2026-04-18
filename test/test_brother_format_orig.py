"""
test_brother_format.py — pytest suite for brother_format.py

Run with:
    pytest test/test_brother_format.py -v
"""

import random

import pytest

import app.brother_format as bf

# Shorthand
KH930 = bf.MachineModel.KH930
KH940 = bf.MachineModel.KH940

# ---------------------------------------------------------------------------
# Geometry helpers (model-independent)
# ---------------------------------------------------------------------------


class TestGeometry:
    def test_nibbles_per_row_exact_multiple(self):
        assert bf.nibbles_per_row(4) == 1
        assert bf.nibbles_per_row(8) == 2
        assert bf.nibbles_per_row(60) == 15
        assert bf.nibbles_per_row(200) == 50

    def test_nibbles_per_row_rounds_up(self):
        assert bf.nibbles_per_row(1) == 1
        assert bf.nibbles_per_row(3) == 1
        assert bf.nibbles_per_row(5) == 2
        assert bf.nibbles_per_row(7) == 2

    def test_bytes_per_pattern_known_value(self):
        # 60 stitches × 150 rows: 15 nibbles/row × 150 = 2250 nibbles → 1125 bytes
        assert bf.bytes_per_pattern(60, 150) == 1125

    def test_bytes_per_pattern_odd_nibble_count_rounds_up(self):
        # 4 stitches (1 nibble/row) × 5 rows = 5 nibbles → rounds to 6 → 3 bytes
        assert bf.bytes_per_pattern(4, 5) == 3

    def test_bytes_per_pattern_small(self):
        assert bf.bytes_per_pattern(5, 4) == 4

    def test_bytes_for_memo(self):
        assert bf.bytes_for_memo(150) == 75
        assert bf.bytes_for_memo(1) == 1
        assert bf.bytes_for_memo(3) == 2  # rounds up to even


# ---------------------------------------------------------------------------
# BCD helpers
# ---------------------------------------------------------------------------


class TestBCD:
    @pytest.mark.parametrize("value", [0, 1, 9, 99, 150, 200, 901, 999])
    def test_roundtrip(self, value):
        h, t, o = bf._bcd_encode_3digit(value)
        assert bf._bcd_decode_3digit(h, t, o) == value

    def test_out_of_range_raises(self):
        with pytest.raises(ValueError):
            bf._bcd_encode_3digit(1000)
        with pytest.raises(ValueError):
            bf._bcd_encode_3digit(-1)

    def test_digit_values_are_in_range(self):
        for v in range(0, 1000):
            h, t, o = bf._bcd_encode_3digit(v)
            assert 0 <= h <= 9
            assert 0 <= t <= 9
            assert 0 <= o <= 9


# ---------------------------------------------------------------------------
# Nibble read/write
# ---------------------------------------------------------------------------


class TestNibbleIO:
    def test_write_and_read_lsn_msn(self):
        buf = bytearray(4)
        base = 3
        bf._write_nibble(buf, base, 0, 0xA)  # nibble 0 → LSN of buf[3]
        bf._write_nibble(buf, base, 1, 0xB)  # nibble 1 → MSN of buf[3]
        bf._write_nibble(buf, base, 2, 0xC)  # nibble 2 → LSN of buf[2]
        bf._write_nibble(buf, base, 3, 0xD)  # nibble 3 → MSN of buf[2]
        assert buf[3] == 0xBA
        assert buf[2] == 0xDC
        assert bf._read_nibble(buf, base, 0) == 0xA
        assert bf._read_nibble(buf, base, 1) == 0xB
        assert bf._read_nibble(buf, base, 2) == 0xC
        assert bf._read_nibble(buf, base, 3) == 0xD

    def test_write_preserves_other_nibble(self):
        buf = bytearray(1)
        base = 0
        bf._write_nibble(buf, base, 0, 0x5)  # write LSN
        bf._write_nibble(buf, base, 1, 0x3)  # write MSN — LSN must be preserved
        assert buf[0] == 0x35
        assert bf._read_nibble(buf, base, 0) == 0x5
        assert bf._read_nibble(buf, base, 1) == 0x3

    def test_value_out_of_range_raises(self):
        buf = bytearray(1)
        with pytest.raises(ValueError):
            bf._write_nibble(buf, 0, 0, 16)
        with pytest.raises(ValueError):
            bf._write_nibble(buf, 0, 0, -1)

    def test_roundtrip_all_nibble_values(self):
        buf = bytearray(8)
        base = 7
        for i in range(16):
            for v in range(16):
                bf._write_nibble(buf, base, i, v)
                assert bf._read_nibble(buf, base, i) == v


# ---------------------------------------------------------------------------
# Row encode / decode
# ---------------------------------------------------------------------------


class TestRowCodec:
    def test_known_encoding(self):
        # pixels [1,0,1,1, 0,1,0,0] → nibble0 = 0b1101=0xD, nibble1 = 0b0010=0x2
        row = [1, 0, 1, 1, 0, 1, 0, 0]
        nibs = bf.encode_row(row, 8)
        assert nibs == [0xD, 0x2]

    def test_roundtrip_8_stitches(self):
        row = [1, 0, 1, 1, 0, 1, 0, 0]
        assert bf.decode_row(bf.encode_row(row, 8), 8) == row

    def test_padding_stitches_not_multiple_of_4(self):
        # 3 stitches: [1,0,1] → padded to [1,0,1,0] → nibble = 0b0101 = 0x5
        row = [1, 0, 1]
        nibs = bf.encode_row(row, 3)
        assert nibs == [0x5]
        assert bf.decode_row(nibs, 3) == row

    def test_all_zeros(self):
        row = [0] * 8
        nibs = bf.encode_row(row, 8)
        assert all(n == 0 for n in nibs)
        assert bf.decode_row(nibs, 8) == row

    def test_all_ones(self):
        row = [1] * 8
        nibs = bf.encode_row(row, 8)
        assert all(n == 0xF for n in nibs)
        assert bf.decode_row(nibs, 8) == row

    def test_wrong_length_raises(self):
        with pytest.raises(ValueError):
            bf.encode_row([1, 0], 4)  # 2 pixels, expects 4

    def test_lsb_is_leftmost_stitch(self):
        # Only stitch 0 (leftmost) is set → bit 0 of nibble should be 1
        row = [1, 0, 0, 0]
        nibs = bf.encode_row(row, 4)
        assert nibs == [0x1]

    def test_msb_of_nibble_is_rightmost_stitch(self):
        # Only stitch 3 (rightmost in group) is set → bit 3 of nibble = 1
        row = [0, 0, 0, 1]
        nibs = bf.encode_row(row, 4)
        assert nibs == [0x8]

    @pytest.mark.parametrize("stitches", [1, 3, 4, 5, 7, 8, 60, 200])
    def test_roundtrip_various_widths(self, stitches):
        random.seed(stitches)
        row = [random.randint(0, 1) for _ in range(stitches)]
        assert bf.decode_row(bf.encode_row(row, stitches), stitches) == row


# ---------------------------------------------------------------------------
# Pattern data encode / decode
# ---------------------------------------------------------------------------


class TestPatternCodec:
    def test_roundtrip_small(self):
        pixel_rows = [[1, 0, 1, 0], [0, 1, 0, 1], [1, 1, 0, 0]]
        encoded = bf.encode_pattern_data(pixel_rows, 4, 3)
        assert len(encoded) == bf.bytes_per_pattern(4, 3)

        buf = bytearray(bf.KH930_WORKING_REGION_SIZE)
        offset = bf.KH930_INIT_PATTERN_OFFSET
        start = offset - len(encoded) + 1
        buf[start : offset + 1] = encoded

        decoded = bf.decode_pattern_data(buf, offset, 4, 3)
        assert decoded == pixel_rows

    def test_roundtrip_random(self):
        random.seed(42)
        stitches, rows = 7, 5
        pixel_rows = [
            [random.randint(0, 1) for _ in range(stitches)] for _ in range(rows)
        ]
        encoded = bf.encode_pattern_data(pixel_rows, stitches, rows)
        assert len(encoded) == bf.bytes_per_pattern(stitches, rows)

        buf = bytearray(bf.KH930_WORKING_REGION_SIZE)
        offset = bf.KH930_INIT_PATTERN_OFFSET
        buf[offset - len(encoded) + 1 : offset + 1] = encoded
        assert bf.decode_pattern_data(buf, offset, stitches, rows) == pixel_rows

    def test_encoded_length_matches_geometry(self):
        for stitches in [4, 5, 7, 60, 200]:
            for rows in [1, 3, 10, 150]:
                rows_data = [[0] * stitches for _ in range(rows)]
                encoded = bf.encode_pattern_data(rows_data, stitches, rows)
                assert len(encoded) == bf.bytes_per_pattern(stitches, rows)

    def test_wrong_row_count_raises(self):
        with pytest.raises(ValueError):
            bf.encode_pattern_data([[1, 0, 1, 0]], 4, 3)  # 1 row, claims 3

    def test_wrong_stitch_count_raises(self):
        with pytest.raises(ValueError):
            bf.encode_pattern_data([[1, 0]], 4, 1)  # 2 stitches, claims 4


# ---------------------------------------------------------------------------
# Directory entry encode / decode — KH-930
# ---------------------------------------------------------------------------


class TestDirectoryEntry930:
    def test_roundtrip(self):
        offset, raw = bf.encode_directory_entry(
            slot_index=0,
            number=901,
            stitches=60,
            rows=150,
            memo_offset=bf.KH930_INIT_PATTERN_OFFSET,
            data_length=bf.KH930_WORKING_REGION_SIZE,
        )
        assert offset == 0
        assert len(raw) == bf.DIRECTORY_ENTRY_SIZE

        entry = bf.decode_directory_entry(raw)
        assert entry is not None
        assert entry.number == 901
        assert entry.stitches == 60
        assert entry.rows == 150

    def test_empty_slot_returns_none(self):
        assert bf.decode_directory_entry(bytes(bf.DIRECTORY_ENTRY_SIZE)) is None

    def test_memo_offset_roundtrip(self):
        memo_offset = bf.KH930_INIT_PATTERN_OFFSET
        offset, raw = bf.encode_directory_entry(
            slot_index=0,
            number=901,
            stitches=4,
            rows=3,
            memo_offset=memo_offset,
            data_length=bf.KH930_WORKING_REGION_SIZE,
        )
        entry = bf.decode_directory_entry(raw)
        assert entry.memo_offset == memo_offset

    def test_slot_index_sets_byte_offset(self):
        _, raw0 = bf.encode_directory_entry(
            0, 901, 4, 3, bf.KH930_INIT_PATTERN_OFFSET, bf.KH930_WORKING_REGION_SIZE
        )
        off1, raw1 = bf.encode_directory_entry(
            1, 902, 4, 3, 0x06D0, bf.KH930_WORKING_REGION_SIZE
        )
        assert off1 == bf.DIRECTORY_ENTRY_SIZE

    def test_invalid_pattern_number_raises(self):
        with pytest.raises(ValueError):
            bf.encode_directory_entry(
                0, 900, 4, 3, bf.KH930_INIT_PATTERN_OFFSET, bf.KH930_WORKING_REGION_SIZE
            )
        with pytest.raises(ValueError):
            bf.encode_directory_entry(
                0,
                1000,
                4,
                3,
                bf.KH930_INIT_PATTERN_OFFSET,
                bf.KH930_WORKING_REGION_SIZE,
            )

    def test_invalid_stitch_count_raises(self):
        with pytest.raises(ValueError):
            bf.encode_directory_entry(
                0, 901, 0, 3, bf.KH930_INIT_PATTERN_OFFSET, bf.KH930_WORKING_REGION_SIZE
            )
        with pytest.raises(ValueError):
            bf.encode_directory_entry(
                0,
                901,
                201,
                3,
                bf.KH930_INIT_PATTERN_OFFSET,
                bf.KH930_WORKING_REGION_SIZE,
            )


# ---------------------------------------------------------------------------
# Directory entry encode / decode — KH-940
# ---------------------------------------------------------------------------


class TestDirectoryEntry940:
    def test_roundtrip(self):
        offset, raw = bf.encode_directory_entry_940(
            slot_index=0,
            number=901,
            stitches=60,
            rows=150,
            memo_offset=bf.KH940_INIT_PATTERN_OFFSET,
        )
        assert offset == 0
        assert len(raw) == bf.DIRECTORY_ENTRY_SIZE

        entry = bf.decode_directory_entry_940(raw)
        assert entry is not None
        assert entry.number == 901
        assert entry.stitches == 60
        assert entry.rows == 150

    def test_memo_offset_roundtrip(self):
        memo_offset = bf.KH940_INIT_PATTERN_OFFSET
        _, raw = bf.encode_directory_entry_940(
            slot_index=0,
            number=901,
            stitches=4,
            rows=3,
            memo_offset=memo_offset,
        )
        entry = bf.decode_directory_entry_940(raw)
        assert entry.memo_offset == memo_offset

    def test_fill_byte_slot_returns_none(self):
        raw = bytes([bf.KH940_FILL_BYTE] * bf.DIRECTORY_ENTRY_SIZE)
        assert bf.decode_directory_entry_940(raw) is None

    def test_finhdr_returns_none(self):
        _, raw = bf._encode_finhdr_940(slot_index=1, next_number=902)
        assert bf.decode_directory_entry_940(raw) is None

    def test_slot_index_sets_byte_offset(self):
        off1, _ = bf.encode_directory_entry_940(
            1, 902, 4, 3, bf.KH940_INIT_PATTERN_OFFSET - 10
        )
        assert off1 == bf.DIRECTORY_ENTRY_SIZE

    def test_pointer_is_16bit_binary_not_bcd(self):
        """DATA_OFFSET must be a plain 16-bit value, not BCD."""
        memo_offset = bf.KH940_INIT_PATTERN_OFFSET
        expected_offset = bf.KH940_REVERSED_BASE - memo_offset
        _, raw = bf.encode_directory_entry_940(0, 901, 4, 3, memo_offset)
        stored = (raw[0] << 8) | raw[1]
        assert stored == expected_offset

    def test_invalid_pattern_number_raises(self):
        with pytest.raises(ValueError):
            bf.encode_directory_entry_940(0, 900, 4, 3, bf.KH940_INIT_PATTERN_OFFSET)
        with pytest.raises(ValueError):
            bf.encode_directory_entry_940(0, 1000, 4, 3, bf.KH940_INIT_PATTERN_OFFSET)

    def test_invalid_stitch_count_raises(self):
        with pytest.raises(ValueError):
            bf.encode_directory_entry_940(0, 901, 0, 3, bf.KH940_INIT_PATTERN_OFFSET)
        with pytest.raises(ValueError):
            bf.encode_directory_entry_940(0, 901, 201, 3, bf.KH940_INIT_PATTERN_OFFSET)


# ---------------------------------------------------------------------------
# DiskImage — KH-930
# ---------------------------------------------------------------------------


class TestDiskImage930:
    def test_blank_has_no_patterns(self):
        img = bf.DiskImage.blank(KH930)
        assert img.list_patterns() == []

    def test_working_region_size(self):
        img = bf.DiskImage.blank(KH930)
        assert len(img.working_region_bytes()) == bf.KH930_WORKING_REGION_SIZE

    def test_write_and_read_pattern(self):
        img = bf.DiskImage.blank(KH930)
        pat = [[1, 0, 1, 0], [0, 1, 0, 1], [1, 1, 0, 0]]
        img.write_pattern(901, pat)
        assert img.read_pattern(901) == pat

    def test_write_multiple_patterns(self):
        img = bf.DiskImage.blank(KH930)
        pat1 = [[1, 0, 1, 0], [0, 1, 0, 1]]
        pat2 = [[0, 0, 1, 1, 0, 0], [1, 1, 0, 0, 1, 1]]
        img.write_pattern(901, pat1)
        img.write_pattern(902, pat2)
        assert img.read_pattern(901) == pat1
        assert img.read_pattern(902) == pat2

    def test_list_patterns(self):
        img = bf.DiskImage.blank(KH930)
        img.write_pattern(901, [[1, 0, 1, 0]])
        img.write_pattern(950, [[0, 1, 0, 1]])
        numbers = [e.number for e in img.list_patterns()]
        assert numbers == [901, 950]

    def test_read_nonexistent_raises(self):
        img = bf.DiskImage.blank(KH930)
        with pytest.raises(KeyError):
            img.read_pattern(901)

    def test_duplicate_pattern_number_raises(self):
        img = bf.DiskImage.blank(KH930)
        img.write_pattern(901, [[1, 0, 1, 0]])
        with pytest.raises(ValueError):
            img.write_pattern(901, [[0, 1, 0, 1]])

    def test_full_image_raises(self):
        img = bf.DiskImage.blank(KH930)
        for n in range(901, 901 + bf.KH930_MAX_PATTERNS):
            img.write_pattern(n, [[1, 0, 1, 0]])
        with pytest.raises(ValueError):
            img.write_pattern(901 + bf.KH930_MAX_PATTERNS, [[1, 0, 1, 0]])

    def test_roundtrip_via_bytes(self):
        img = bf.DiskImage.blank(KH930)
        pat1 = [[1, 0, 1, 0], [0, 1, 0, 1]]
        pat2 = [[1, 1, 0, 0]]
        img.write_pattern(901, pat1)
        img.write_pattern(902, pat2)

        raw = img.working_region_bytes()
        img2 = bf.DiskImage.from_bytes(raw, KH930)
        assert img2.read_pattern(901) == pat1
        assert img2.read_pattern(902) == pat2

    def test_200_stitch_pattern(self):
        img = bf.DiskImage.blank(KH930)
        wide = [[i % 2 for i in range(200)]]
        img.write_pattern(901, wide)
        assert img.read_pattern(901) == wide

    def test_to_sector_files(self):
        img = bf.DiskImage.blank(KH930)
        img.write_pattern(901, [[1, 0, 1, 0]])
        sectors = img.to_sector_files()
        assert len(sectors) == 80
        assert all(len(sectors[n]) == bf.SECTOR_SIZE for n in range(80))
        # Sectors 0+1 reconstruct working region
        assert sectors[0] + sectors[1] == img.working_region_bytes()
        # Sectors 2–79 are zeros
        for n in range(2, 80):
            assert sectors[n] == bytes(bf.SECTOR_SIZE)

    def test_to_disk_image_bytes_length(self):
        img = bf.DiskImage.blank(KH930)
        assert len(img.to_disk_image_bytes()) == bf.DISK_IMAGE_SIZE

    def test_to_disk_image_bytes_content(self):
        img = bf.DiskImage.blank(KH930)
        img.write_pattern(901, [[1, 0, 1, 0]])
        raw = img.to_disk_image_bytes()
        wr = img.working_region_bytes()
        assert raw[: bf.KH930_WORKING_REGION_SIZE] == wr
        assert raw[bf.KH930_WORKING_REGION_SIZE :] == bytes(
            bf.DISK_IMAGE_SIZE - bf.KH930_WORKING_REGION_SIZE
        )

    def test_from_bytes_full_disk_image(self):
        img = bf.DiskImage.blank(KH930)
        pat = [[1, 0, 1, 0], [0, 1, 0, 1]]
        img.write_pattern(901, pat)
        full = img.to_disk_image_bytes()
        img2 = bf.DiskImage.from_bytes(full, KH930)
        assert img2.read_pattern(901) == pat

    def test_from_bytes_too_short_raises(self):
        with pytest.raises(ValueError):
            bf.DiskImage.from_bytes(bytes(100), KH930)

    def test_random_pattern_roundtrip(self):
        random.seed(99)
        img = bf.DiskImage.blank(KH930)
        stitches, rows = 13, 8
        pat = [[random.randint(0, 1) for _ in range(stitches)] for _ in range(rows)]
        img.write_pattern(901, pat)
        raw = img.working_region_bytes()
        img2 = bf.DiskImage.from_bytes(raw, KH930)
        assert img2.read_pattern(901) == pat


# ---------------------------------------------------------------------------
# DiskImage — KH-940
# ---------------------------------------------------------------------------


class TestDiskImage940:
    def test_blank_has_no_patterns(self):
        img = bf.DiskImage.blank(KH940)
        assert img.list_patterns() == []

    def test_default_model_is_kh940(self):
        img = bf.DiskImage.blank()
        assert img.model == KH940

    def test_working_region_size(self):
        img = bf.DiskImage.blank(KH940)
        assert len(img.working_region_bytes()) == bf.KH940_WORKING_REGION_SIZE

    def test_to_disk_image_bytes_length(self):
        img = bf.DiskImage.blank(KH940)
        assert len(img.to_disk_image_bytes()) == bf.DISK_IMAGE_SIZE

    def test_working_sectors_in_disk_image(self):
        img = bf.DiskImage.blank(KH940)
        img.write_pattern(901, [[1, 0, 1, 0]])
        raw = img.to_disk_image_bytes()
        wr = img.working_region_bytes()
        # First 32 sectors = working region
        assert raw[: bf.KH940_WORKING_REGION_SIZE] == wr
        # Sectors 32–79 are zero-padded
        assert raw[bf.KH940_WORKING_REGION_SIZE :] == bytes(
            bf.DISK_IMAGE_SIZE - bf.KH940_WORKING_REGION_SIZE
        )

    def test_to_sector_files_940(self):
        img = bf.DiskImage.blank(KH940)
        img.write_pattern(901, [[1, 0, 1, 0]])
        sectors = img.to_sector_files()
        assert len(sectors) == 80
        # First 32 sectors contain the working region
        working = b"".join(sectors[n] for n in range(32))
        assert working == img.working_region_bytes()
        # Sectors 32–79 are zeros
        for n in range(32, 80):
            assert sectors[n] == bytes(bf.SECTOR_SIZE)

    def test_write_and_read_pattern(self):
        img = bf.DiskImage.blank(KH940)
        pat = [[1, 0, 1, 0], [0, 1, 0, 1], [1, 1, 0, 0]]
        img.write_pattern(901, pat)
        assert img.read_pattern(901) == pat

    def test_write_multiple_patterns(self):
        img = bf.DiskImage.blank(KH940)
        pat1 = [[1, 0, 1, 0], [0, 1, 0, 1]]
        pat2 = [[0, 0, 1, 1, 0, 0], [1, 1, 0, 0, 1, 1]]
        img.write_pattern(901, pat1)
        img.write_pattern(902, pat2)
        assert img.read_pattern(901) == pat1
        assert img.read_pattern(902) == pat2

    def test_list_patterns(self):
        img = bf.DiskImage.blank(KH940)
        img.write_pattern(901, [[1, 0, 1, 0]])
        img.write_pattern(950, [[0, 1, 0, 1]])
        numbers = [e.number for e in img.list_patterns()]
        assert numbers == [901, 950]

    def test_read_nonexistent_raises(self):
        img = bf.DiskImage.blank(KH940)
        with pytest.raises(KeyError):
            img.read_pattern(901)

    def test_duplicate_pattern_number_raises(self):
        img = bf.DiskImage.blank(KH940)
        img.write_pattern(901, [[1, 0, 1, 0]])
        with pytest.raises(ValueError):
            img.write_pattern(901, [[0, 1, 0, 1]])

    def test_full_image_raises(self):
        img = bf.DiskImage.blank(KH940)
        for n in range(901, 901 + bf.KH940_MAX_PATTERNS):
            img.write_pattern(n, [[1, 0, 1, 0]])
        with pytest.raises(ValueError):
            img.write_pattern(901 + bf.KH940_MAX_PATTERNS, [[1, 0, 1, 0]])

    def test_roundtrip_via_bytes(self):
        img = bf.DiskImage.blank(KH940)
        pat1 = [[1, 0, 1, 0], [0, 1, 0, 1]]
        pat2 = [[1, 1, 0, 0]]
        img.write_pattern(901, pat1)
        img.write_pattern(902, pat2)

        raw = img.working_region_bytes()
        img2 = bf.DiskImage.from_bytes(raw, KH940)
        assert img2.read_pattern(901) == pat1
        assert img2.read_pattern(902) == pat2

    def test_200_stitch_pattern(self):
        img = bf.DiskImage.blank(KH940)
        wide = [[i % 2 for i in range(200)]]
        img.write_pattern(901, wide)
        assert img.read_pattern(901) == wide

    def test_from_bytes_too_short_raises(self):
        with pytest.raises(ValueError):
            bf.DiskImage.from_bytes(bytes(100), KH940)

    def test_random_pattern_roundtrip(self):
        random.seed(99)
        img = bf.DiskImage.blank(KH940)
        stitches, rows = 13, 8
        pat = [[random.randint(0, 1) for _ in range(stitches)] for _ in range(rows)]
        img.write_pattern(901, pat)
        raw = img.working_region_bytes()
        img2 = bf.DiskImage.from_bytes(raw, KH940)
        assert img2.read_pattern(901) == pat

    def test_last_byte_is_0x02(self):
        img = bf.DiskImage.blank(KH940)
        assert img._data[0x7FFF] == 0x02

    def test_unused_directory_slots_are_fill_byte(self):
        img = bf.DiskImage.blank(KH940)
        # Before any writes, slot 0 should be all 0x55
        slot0 = img._data[0:7]
        assert all(b == bf.KH940_FILL_BYTE for b in slot0)

    def test_finhdr_written_after_pattern(self):
        img = bf.DiskImage.blank(KH940)
        img.write_pattern(901, [[1, 0, 1, 0]])
        # Slot 1 should be the FINHDR (first byte = 0x55)
        finhdr = img._data[bf.DIRECTORY_ENTRY_SIZE : 2 * bf.DIRECTORY_ENTRY_SIZE]
        assert finhdr[0] == bf.KH940_FILL_BYTE

    def test_control_data_unk1_is_0x0001(self):
        img = bf.DiskImage.blank(KH940)
        img.write_pattern(901, [[1, 0, 1, 0]])
        base = bf.KH940_CONTROL_DATA_ADDR
        unk1 = (img._data[base + 2] << 8) | img._data[base + 3]
        assert unk1 == 0x0001

    def test_control_data_unk3_is_0x00008100(self):
        img = bf.DiskImage.blank(KH940)
        img.write_pattern(901, [[1, 0, 1, 0]])
        base = bf.KH940_CONTROL_DATA_ADDR
        unk3 = (
            (img._data[base + 0x0C] << 24)
            | (img._data[base + 0x0D] << 16)
            | (img._data[base + 0x0E] << 8)
            | img._data[base + 0x0F]
        )
        assert unk3 == 0x00008100

    def test_loaded_pattern_reflects_last_written(self):
        img = bf.DiskImage.blank(KH940)
        img.write_pattern(901, [[1, 0, 1, 0]])
        img.write_pattern(942, [[0, 1, 0, 1]])
        b0 = img._data[bf.KH940_LOADED_PATTERN_ADDR]
        b1 = img._data[bf.KH940_LOADED_PATTERN_ADDR + 1]
        upper = (b0 & 0xF0) >> 4
        assert upper == 0x1  # XX nibble always 1
        ph = b0 & 0x0F
        pt = (b1 & 0xF0) >> 4
        po = b1 & 0x0F
        number = bf._bcd_decode_3digit(ph, pt, po)
        assert number == 942

    def test_from_bytes_full_disk_image(self):
        img = bf.DiskImage.blank(KH940)
        pat = [[1, 0, 1, 0], [0, 1, 0, 1]]
        img.write_pattern(901, pat)
        full = img.to_disk_image_bytes()
        img2 = bf.DiskImage.from_bytes(full, KH940)
        assert img2.read_pattern(901) == pat

    def test_large_pattern_fits_in_940_not_930(self):
        """A large pattern that overflows KH-930 memory should fit in KH-940."""
        # 200 stitches × 400 rows ≈ 20,000 bytes — well beyond the KH-930's
        # ~1,300 bytes of usable pattern space.
        stitches, rows = 200, 400
        pat = [[i % 2 for i in range(stitches)] for _ in range(rows)]

        img_940 = bf.DiskImage.blank(KH940)
        img_940.write_pattern(901, pat)
        assert img_940.read_pattern(901) == pat

        img_930 = bf.DiskImage.blank(KH930)
        with pytest.raises(ValueError):
            img_930.write_pattern(901, pat)

    def test_pattern_memory_does_not_overlap_directory(self):
        """Pattern data must stay above the directory region (0x02AD)."""
        img = bf.DiskImage.blank(KH940)
        pat = [[i % 2 for i in range(200)] for _ in range(400)]
        img.write_pattern(901, pat)
        entry = img.get_pattern_entry(901)
        assert entry.block_end_offset > 0x02AD


# ---------------------------------------------------------------------------
# Backwards-compatibility aliases
# ---------------------------------------------------------------------------


class TestBackwardsCompatAliases:
    """The old module-level names should still resolve to the KH-930 values."""

    def test_working_region_size_alias(self):
        assert bf.WORKING_REGION_SIZE == bf.KH930_WORKING_REGION_SIZE

    def test_max_patterns_alias(self):
        assert bf.MAX_PATTERNS == bf.KH930_MAX_PATTERNS

    def test_init_pattern_offset_alias(self):
        assert bf.INIT_PATTERN_OFFSET == bf.KH930_INIT_PATTERN_OFFSET

    def test_disk_image_blank_default_is_940(self):
        img = bf.DiskImage.blank()
        assert img.model == KH940
