"""
brother_format.py — Brother KH-930E disk image format encoder/decoder.

Binary format reference (reverse-engineered by Steve Conklin, Travis Goodspeed,
and others; documented in https://github.com/stg/knittington/blob/master/doc/kh940_format.txt).

DISK IMAGE LAYOUT
-----------------
A full disk image is 81,920 bytes: 80 sectors × 1,024 bytes each.
The machine treats this as a flat 2,048-byte address space (the first two sectors,
00.dat and 01.dat, are the only ones that matter for pattern data).

ADDRESS SPACE (0x0000–0x07FF, within the 2,048-byte working region)
--------------------------------------------------------------------
0x0000–0x0452   Pattern directory: 99 entries × 7 bytes each (BCD-encoded metadata)
0x06DF          initPatternOffset: pattern data starts here, grows DOWNWARD
0x06FF          currentRowAddr
0x0702          currentRowNumberAddr
0x070F          carriageStatusAddr
0x072F          nextRowAddr
0x07EA          currentPatternAddr / selectAddr
0x07FB–0x07FE   motif data
0x07FE–0x07FF   patternPosition

PATTERN DIRECTORY ENTRY (7 bytes per pattern, 99 entries, index 0–98)
----------------------------------------------------------------------
Each entry uses BCD (Binary Coded Decimal) encoding: each decimal digit
occupies one nibble (4 bits).

  Byte 0:   flag      — 0x00 = empty slot; nonzero = valid pattern.
                        In "pointer mode" the flag byte is also the high byte
                        of a 16-bit reversed-address pointer (see below).
  Byte 1:   unknown   — Low byte of the reversed-address pointer (pointer mode).
  Byte 2:   rh:rt     — MSN=rows hundreds, LSN=rows tens
  Byte 3:   ro:sh     — MSN=rows ones,    LSN=stitches hundreds
  Byte 4:   st:so     — MSN=stitches tens, LSN=stitches ones
  Byte 5:   unk:ph    — MSN=unused,        LSN=pattern number hundreds
  Byte 6:   pt:po     — MSN=pattern number tens, LSN=pattern number ones

  Pattern numbers are 901–999 (custom patterns).

POINTER MODE (the correct mode for KH-930/940)
-----------------------------------------------
The flag:unknown pair encodes a reversed-address pointer:

    memo_offset = len(data) - 1 - ((flag << 8) | unknown)

"Reversed address" means offset 0 points to the LAST byte of the file,
offset 1 points to the second-to-last byte, etc.  Pattern data therefore
grows DOWNWARD from 0x06DF toward lower addresses.

PATTERN DATA LAYOUT (within the 2,048-byte region)
---------------------------------------------------
For each valid pattern entry, from memo_offset downward:

  [memo block]    bytesForMemo(rows) bytes     — at memo_offset, grows down
  [pattern data]  bytesPerPattern(stitches, rows) bytes — immediately below memo

NIBBLE AND BYTE GEOMETRY
-------------------------
  nibblesPerRow(stitches) = ceil4(stitches) / 4
      Each row is nibble-aligned: stitch count rounded UP to multiple of 4.

  bytesPerPattern(stitches, rows) = ceil2(rows × nibblesPerRow(stitches)) / 2
      Total nibbles for all rows, rounded UP to whole bytes.

  bytesForMemo(rows) = ceil2(rows) / 2
      One nibble per row, byte-aligned.

NIBBLE ADDRESSING (within a block at base offset `base`)
---------------------------------------------------------
Nibbles are numbered 0, 1, 2, ... starting from base and advancing BACKWARD:

  nibble N lives in byte at address:  base - (N // 2)
  within that byte:
    even N → LSN (bits 3:0)
    odd  N → MSN (bits 7:4)

STITCH BIT ORDER WITHIN A NIBBLE
----------------------------------
  bit 0 (LSB) → stitch 0 (leftmost in the group of 4)
  bit 1       → stitch 1
  bit 2       → stitch 2
  bit 3       → stitch 3 (rightmost in the group of 4)

  stitch value 1 = needle selected (knit); 0 = not selected (skip).

ROW ORDER
---------
Row 0 is the FIRST row knitted (bottom of the physical fabric on most machines,
but this matches the top of the source image per the insertpattern.py convention).
Rows are stored sequentially in nibble-space: row 0 occupies nibbles 0..(npr-1),
row 1 occupies nibbles npr..(2*npr-1), etc.

MEMO BLOCK
----------
One nibble per row, same backward nibble addressing, same bit-order within nibble.
The meaning of memo nibble bits is not fully documented; it appears to encode
per-row color/selector metadata. Written as zeros for simple two-color patterns.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SECTOR_SIZE: int = 1024
NUM_SECTORS: int = 80
DISK_IMAGE_SIZE: int = NUM_SECTORS * SECTOR_SIZE  # 81,920 bytes

# The first two sectors are the 2,048-byte working region the machine reads.
WORKING_REGION_SIZE: int = 2 * SECTOR_SIZE  # 2,048 bytes

DIRECTORY_ENTRY_SIZE: int = 7
MAX_PATTERNS: int = 99
PATTERN_NUMBER_MIN: int = 901
PATTERN_NUMBER_MAX: int = 999

# Patterns start here and grow DOWNWARD.
INIT_PATTERN_OFFSET: int = 0x06DF

# Well-known addresses within the 2,048-byte working region.
CURRENT_PATTERN_ADDR: int = 0x07EA
CURRENT_ROW_ADDR: int = 0x06FF
NEXT_ROW_ADDR: int = 0x072F
CURRENT_ROW_NUMBER_ADDR: int = 0x0702
CARRIAGE_STATUS_ADDR: int = 0x070F
SELECT_ADDR: int = 0x07EA


# ---------------------------------------------------------------------------
# Low-level geometry helpers
# ---------------------------------------------------------------------------


def _ceil4(n: int) -> int:
    """Round n up to the nearest multiple of 4."""
    r = n % 4
    return n if r == 0 else n + (4 - r)


def _ceil2(n: int) -> int:
    """Round n up to the nearest multiple of 2 (i.e. make even)."""
    return n if n % 2 == 0 else n + 1


def nibbles_per_row(stitches: int) -> int:
    """
    Number of nibbles required to store one row of `stitches` stitches.
    Stitch count is rounded up to the nearest multiple of 4 (nibble-aligned).
    """
    return _ceil4(stitches) // 4


def bytes_per_pattern(stitches: int, rows: int) -> int:
    """
    Total bytes required to store pattern pixel data (not including memo).
    """
    nibbles = rows * nibbles_per_row(stitches)
    return _ceil2(nibbles) // 2


def bytes_for_memo(rows: int) -> int:
    """
    Total bytes required for the memo block (1 nibble per row, byte-aligned).
    """
    return _ceil2(rows) // 2


def bytes_per_pattern_and_memo(stitches: int, rows: int) -> int:
    """Total bytes consumed by one pattern entry (data + memo)."""
    return bytes_per_pattern(stitches, rows) + bytes_for_memo(rows)


# ---------------------------------------------------------------------------
# BCD helpers
# ---------------------------------------------------------------------------


def _bcd_encode_3digit(value: int) -> tuple[int, int, int]:
    """
    Encode a 3-digit decimal value as (hundreds, tens, ones) nibbles.
    Each element is 0–9.
    """
    if not (0 <= value <= 999):
        raise ValueError(f"Value {value} out of BCD 3-digit range 0–999")
    hundreds = value // 100
    tens = (value % 100) // 10
    ones = value % 10
    return hundreds, tens, ones


def _bcd_decode_3digit(hundreds: int, tens: int, ones: int) -> int:
    """Decode three BCD nibbles back to an integer."""
    return 100 * hundreds + 10 * tens + ones


# ---------------------------------------------------------------------------
# Nibble-level read/write (backward addressing)
# ---------------------------------------------------------------------------


def _read_nibble(data: bytearray | bytes, base: int, nibble_index: int) -> int:
    """
    Read a single nibble from `data`.

    Nibbles are numbered from `base` and advance BACKWARD through `data`:
      nibble 0 → LSN of data[base]
      nibble 1 → MSN of data[base]
      nibble 2 → LSN of data[base - 1]
      nibble 3 → MSN of data[base - 1]
      ...

    Returns an integer 0–15.
    """
    byte_offset = base - (nibble_index // 2)
    byte_val = data[byte_offset]
    if nibble_index % 2 == 0:
        return byte_val & 0x0F  # LSN
    else:
        return (byte_val & 0xF0) >> 4  # MSN


def _write_nibble(data: bytearray, base: int, nibble_index: int, value: int) -> None:
    """
    Write a single nibble (0–15) into `data` using the same backward addressing
    as _read_nibble.  Only the target nibble is modified; the other nibble in
    the same byte is preserved.
    """
    if not (0 <= value <= 15):
        raise ValueError(f"Nibble value {value} out of range 0–15")
    byte_offset = base - (nibble_index // 2)
    if nibble_index % 2 == 0:
        # Write LSN, preserve MSN
        data[byte_offset] = (data[byte_offset] & 0xF0) | (value & 0x0F)
    else:
        # Write MSN, preserve LSN
        data[byte_offset] = (data[byte_offset] & 0x0F) | ((value & 0x0F) << 4)


# ---------------------------------------------------------------------------
# Row encode / decode
# ---------------------------------------------------------------------------


def encode_row(pixels: Sequence[int], stitches: int) -> list[int]:
    """
    Encode a row of pixel values into a list of nibbles.

    `pixels` must contain exactly `stitches` values, each 0 (background/skip)
    or 1 (selected/knit).  Dark pixels should be passed as 1.

    Returns a list of nibbles (length = nibbles_per_row(stitches)).
    Within each nibble: bit 0 = leftmost stitch, bit 3 = rightmost stitch.
    Padding stitches (if stitches is not a multiple of 4) are filled with 0.
    """
    if len(pixels) != stitches:
        raise ValueError(f"Expected {stitches} pixels for this row, got {len(pixels)}")
    npr = nibbles_per_row(stitches)
    nibble_list: list[int] = []
    padded = list(pixels) + [0] * (_ceil4(stitches) - stitches)
    for n in range(npr):
        s = n * 4
        nibble = (
            (padded[s] & 1)
            | ((padded[s + 1] & 1) << 1)
            | ((padded[s + 2] & 1) << 2)
            | ((padded[s + 3] & 1) << 3)
        )
        nibble_list.append(nibble)
    return nibble_list


def decode_row(nibble_list: Sequence[int], stitches: int) -> list[int]:
    """
    Decode a list of nibbles back into a list of `stitches` pixel values (0 or 1).

    This is the inverse of encode_row.
    """
    npr = nibbles_per_row(stitches)
    if len(nibble_list) < npr:
        raise ValueError(
            f"Need at least {npr} nibbles to decode {stitches} stitches, "
            f"got {len(nibble_list)}"
        )
    pixels: list[int] = []
    remaining = stitches
    for nib in nibble_list[:npr]:
        for bit in range(4):
            if remaining == 0:
                break
            pixels.append((nib >> bit) & 1)
            remaining -= 1
    return pixels


# ---------------------------------------------------------------------------
# Pattern data encode / decode (full pattern ↔ bytes in the working region)
# ---------------------------------------------------------------------------


def encode_pattern_data(
    pixel_rows: Sequence[Sequence[int]],
    stitches: int,
    rows: int,
) -> bytearray:
    """
    Encode a complete pattern (all rows) into a bytearray of length
    bytes_per_pattern(stitches, rows).

    `pixel_rows` is a sequence of rows, each row a sequence of `stitches`
    values (0 or 1).  Row 0 is the first row knitted.

    The returned bytearray uses the same backward nibble addressing as the
    machine's memory: the first row's nibble 0 is in the LSN of the last byte.
    Callers should place this block so that its last byte sits at
    (pattern_offset) in the working region and grows toward lower addresses.
    """
    if len(pixel_rows) != rows:
        raise ValueError(f"Expected {rows} rows, got {len(pixel_rows)}")
    npr = nibbles_per_row(stitches)
    total_nibbles = rows * npr
    total_bytes = _ceil2(total_nibbles) // 2

    # Build the bytearray. We'll use the same nibble-index scheme:
    # nibble 0 → LSN of byte[total_bytes - 1]  (the "base" byte)
    # nibble 1 → MSN of byte[total_bytes - 1]
    # nibble 2 → LSN of byte[total_bytes - 2]
    # ...
    result = bytearray(total_bytes)
    base = total_bytes - 1  # local base within result

    for row_idx, row_pixels in enumerate(pixel_rows):
        row_nibbles = encode_row(row_pixels, stitches)
        for local_nib_idx, nib_val in enumerate(row_nibbles):
            nibble_index = row_idx * npr + local_nib_idx
            _write_nibble(result, base, nibble_index, nib_val)

    return result


def decode_pattern_data(
    data: bytearray | bytes,
    pattern_offset: int,
    stitches: int,
    rows: int,
) -> list[list[int]]:
    """
    Decode a pattern from the working-region bytearray `data`.

    `pattern_offset` is the address of the "base" byte (the highest address
    of the pattern block, which holds nibble 0).

    Returns a list of `rows` lists, each containing `stitches` pixel values
    (0 or 1).
    """
    npr = nibbles_per_row(stitches)
    pixel_rows: list[list[int]] = []
    for row_idx in range(rows):
        nibble_list = [
            _read_nibble(data, pattern_offset, row_idx * npr + local_nib)
            for local_nib in range(npr)
        ]
        pixel_rows.append(decode_row(nibble_list, stitches))
    return pixel_rows


# ---------------------------------------------------------------------------
# Memo encode / decode
# ---------------------------------------------------------------------------


def encode_memo(rows: int, memo_values: Sequence[int] | None = None) -> bytearray:
    """
    Encode a memo block for `rows` rows.

    `memo_values` is an optional sequence of per-row nibble values (0–15).
    If None or shorter than `rows`, missing entries default to 0.

    The returned bytearray has length bytes_for_memo(rows).
    Nibble 0 (LSN of last byte) = memo for row 0.
    """
    values = list(memo_values) if memo_values else []
    values += [0] * (rows - len(values))  # pad to length

    total_bytes = bytes_for_memo(rows)
    result = bytearray(total_bytes)
    base = total_bytes - 1

    for row_idx in range(rows):
        _write_nibble(result, base, row_idx, values[row_idx] & 0x0F)

    return result


def decode_memo(
    data: bytearray | bytes,
    memo_offset: int,
    rows: int,
) -> list[int]:
    """
    Decode the memo block from `data` at `memo_offset`.
    Returns a list of `rows` nibble values (0–15).
    """
    return [_read_nibble(data, memo_offset, row_idx) for row_idx in range(rows)]


# ---------------------------------------------------------------------------
# Directory entry encode / decode
# ---------------------------------------------------------------------------


@dataclass
class PatternEntry:
    """Metadata for one pattern stored in the Brother directory."""

    number: int  # 901–999
    stitches: int  # 1–200
    rows: int  # 1–999
    flag: int  # high byte of reversed-address pointer
    pointer_low: int  # low byte of reversed-address pointer

    @property
    def memo_offset(self) -> int:
        """
        Absolute offset of the memo block's base byte within the working region.
        Computed from the reversed-address pointer:
          memo_offset = (WORKING_REGION_SIZE - 1) - ((flag << 8) | pointer_low)
        """
        return (WORKING_REGION_SIZE - 1) - ((self.flag << 8) | self.pointer_low)

    @property
    def pattern_offset(self) -> int:
        """Absolute offset of the pattern data's base byte (just below memo)."""
        return self.memo_offset - bytes_for_memo(self.rows)

    @property
    def block_end_offset(self) -> int:
        """
        Absolute offset of the byte just below the entire pattern+memo block.
        The next pattern's memo_offset will be at or above this address.
        """
        return self.memo_offset - bytes_per_pattern_and_memo(self.stitches, self.rows)


def encode_directory_entry(
    slot_index: int,
    number: int,
    stitches: int,
    rows: int,
    memo_offset: int,
    data_length: int,
) -> tuple[int, bytes]:
    """
    Encode one 7-byte directory entry.

    `slot_index` is 0-based (0 = first pattern slot, bytes 0–6 of the file).
    `memo_offset` is the absolute byte offset of this pattern's memo base.
    `data_length` is len(working_region) = WORKING_REGION_SIZE.

    Returns (byte_offset_in_file, 7_bytes).
    """
    if not (PATTERN_NUMBER_MIN <= number <= PATTERN_NUMBER_MAX):
        raise ValueError(
            f"Pattern number {number} out of range "
            f"{PATTERN_NUMBER_MIN}–{PATTERN_NUMBER_MAX}"
        )
    if not (1 <= stitches <= 200):
        raise ValueError(f"Stitch count {stitches} out of range 1–200")
    if not (1 <= rows <= 999):
        raise ValueError(f"Row count {rows} out of range 1–999")

    # Reversed-address pointer
    reversed_addr = (data_length - 1) - memo_offset
    flag = (reversed_addr >> 8) & 0xFF
    ptr_low = reversed_addr & 0xFF

    rh, rt, ro = _bcd_encode_3digit(rows)
    sh, st, so = _bcd_encode_3digit(stitches)
    ph, pt, po = _bcd_encode_3digit(number)

    entry = bytes(
        [
            flag,
            ptr_low,
            (rh << 4) | rt,
            (ro << 4) | sh,
            (st << 4) | so,
            (0x0 << 4) | ph,  # upper nibble unused
            (pt << 4) | po,
        ]
    )

    byte_offset = slot_index * DIRECTORY_ENTRY_SIZE
    return byte_offset, entry


def decode_directory_entry(raw: bytes | bytearray) -> PatternEntry | None:
    """
    Decode a 7-byte directory entry.
    Returns None if the slot is empty (flag == 0).
    """
    if len(raw) < DIRECTORY_ENTRY_SIZE:
        raise ValueError(
            f"Directory entry must be {DIRECTORY_ENTRY_SIZE} bytes, " f"got {len(raw)}"
        )
    flag = raw[0]
    if flag == 0:
        return None  # empty slot

    ptr_low = raw[1]
    rh = (raw[2] & 0xF0) >> 4
    rt = raw[2] & 0x0F
    ro = (raw[3] & 0xF0) >> 4
    sh = raw[3] & 0x0F
    st = (raw[4] & 0xF0) >> 4
    so = raw[4] & 0x0F
    ph = raw[5] & 0x0F  # upper nibble ignored
    pt = (raw[6] & 0xF0) >> 4
    po = raw[6] & 0x0F

    rows = _bcd_decode_3digit(rh, rt, ro)
    stitches = _bcd_decode_3digit(sh, st, so)
    number = _bcd_decode_3digit(ph, pt, po)

    return PatternEntry(
        number=number,
        stitches=stitches,
        rows=rows,
        flag=flag,
        pointer_low=ptr_low,
    )


# ---------------------------------------------------------------------------
# DiskImage — the top-level object
# ---------------------------------------------------------------------------


@dataclass
class DiskImage:
    """
    An in-memory representation of a Brother KH-930E disk image.

    The full image is DISK_IMAGE_SIZE (81,920) bytes, but only the first two
    sectors (the "working region", 2,048 bytes) contain pattern data.  The
    remaining 78 sectors are populated when writing to disk but are not
    significant for pattern content.

    Usage::

        img = DiskImage.blank()
        img.write_pattern(901, pixel_rows)
        track_files = img.to_track_files()

    """

    # The full 2,048-byte working region.
    _data: bytearray = field(default_factory=lambda: bytearray(WORKING_REGION_SIZE))

    # Next available offset for pattern data (starts at INIT_PATTERN_OFFSET,
    # decrements as patterns are added).
    _next_pattern_ptr: int = field(default=INIT_PATTERN_OFFSET)

    # Slot index for the next directory entry (0-based).
    _next_slot: int = field(default=0)

    # ---------------------------------------------------------------------------
    # Construction
    # ---------------------------------------------------------------------------

    @classmethod
    def blank(cls) -> "DiskImage":
        """
        Create a blank disk image with an empty pattern directory.
        All bytes are initialised to 0x00.
        """
        return cls()

    @classmethod
    def from_bytes(cls, data: bytes | bytearray) -> "DiskImage":
        """
        Load a DiskImage from an existing working-region blob (2,048 bytes)
        or full disk image (81,920 bytes).  Only the first 2,048 bytes are
        used for pattern data.
        """
        if len(data) < WORKING_REGION_SIZE:
            raise ValueError(
                f"Data too short: need at least {WORKING_REGION_SIZE} bytes, "
                f"got {len(data)}"
            )
        working = bytearray(data[:WORKING_REGION_SIZE])
        img = cls(_data=working)
        img._sync_state_from_directory()
        return img

    def _sync_state_from_directory(self) -> None:
        """
        After loading from bytes, scan the directory to find the current
        _next_slot and _next_pattern_ptr so that new patterns can be appended
        correctly.
        """
        for slot in range(MAX_PATTERNS):
            raw = self._data[
                slot * DIRECTORY_ENTRY_SIZE : (slot + 1) * DIRECTORY_ENTRY_SIZE
            ]
            entry = decode_directory_entry(raw)
            if entry is None:
                self._next_slot = slot
                # _next_pattern_ptr is the address just below the last pattern's block.
                # If no patterns exist yet, use INIT_PATTERN_OFFSET.
                break
            self._next_pattern_ptr = entry.block_end_offset
        else:
            # All 99 slots are filled.
            self._next_slot = MAX_PATTERNS

    # ---------------------------------------------------------------------------
    # Reading
    # ---------------------------------------------------------------------------

    def list_patterns(self) -> list[PatternEntry]:
        """Return a list of all valid PatternEntry objects in the directory."""
        entries: list[PatternEntry] = []
        for slot in range(MAX_PATTERNS):
            raw = self._data[
                slot * DIRECTORY_ENTRY_SIZE : (slot + 1) * DIRECTORY_ENTRY_SIZE
            ]
            entry = decode_directory_entry(raw)
            if entry is None:
                break
            entries.append(entry)
        return entries

    def get_pattern_entry(self, number: int) -> PatternEntry | None:
        """Return the PatternEntry for pattern `number`, or None if not found."""
        for entry in self.list_patterns():
            if entry.number == number:
                return entry
        return None

    def read_pattern(self, number: int) -> list[list[int]]:
        """
        Decode and return the pixel data for pattern `number`.

        Returns a list of rows (row 0 first), each row a list of stitches
        (0 = background, 1 = knit).

        Raises KeyError if the pattern is not found.
        """
        entry = self.get_pattern_entry(number)
        if entry is None:
            raise KeyError(f"Pattern {number} not found in disk image")
        return decode_pattern_data(
            self._data, entry.pattern_offset, entry.stitches, entry.rows
        )

    def read_memo(self, number: int) -> list[int]:
        """
        Return the memo nibble values for pattern `number`.
        Raises KeyError if not found.
        """
        entry = self.get_pattern_entry(number)
        if entry is None:
            raise KeyError(f"Pattern {number} not found in disk image")
        return decode_memo(self._data, entry.memo_offset, entry.rows)

    # ---------------------------------------------------------------------------
    # Writing
    # ---------------------------------------------------------------------------

    def write_pattern(
        self,
        number: int,
        pixel_rows: Sequence[Sequence[int]],
        memo_values: Sequence[int] | None = None,
    ) -> PatternEntry:
        """
        Encode and write a new pattern into the disk image.

        `number` must be 901–999 and not already present in the image.
        `pixel_rows` is a list of rows (row 0 = first row to knit), each row
        a list of stitch values (0 = skip, 1 = knit).  All rows must have
        the same length.

        `memo_values` is an optional list of per-row nibble values for the
        memo block; defaults to all zeros.

        Returns the PatternEntry that was written.
        Raises ValueError if the image is full or the pattern number is taken.
        """
        if self._next_slot >= MAX_PATTERNS:
            raise ValueError("Disk image is full (99 patterns already stored)")
        if self.get_pattern_entry(number) is not None:
            raise ValueError(f"Pattern {number} already exists in this disk image")

        rows = len(pixel_rows)
        if rows == 0:
            raise ValueError("pixel_rows must not be empty")
        stitches = len(pixel_rows[0])
        if stitches == 0 or stitches > 200:
            raise ValueError(f"Stitch count {stitches} out of range 1–200")
        for i, row in enumerate(pixel_rows):
            if len(row) != stitches:
                raise ValueError(
                    f"Row {i} has {len(row)} stitches; expected {stitches}"
                )

        # --- Encode data blocks ---
        pat_bytes = encode_pattern_data(pixel_rows, stitches, rows)
        memo_bytes = encode_memo(rows, memo_values)

        total = len(pat_bytes) + len(memo_bytes)
        if self._next_pattern_ptr - total < 0:
            raise ValueError(
                f"Not enough space in disk image for pattern {number} "
                f"({total} bytes needed)"
            )

        # --- Compute offsets ---
        # memo goes first (higher address), then pattern data below it.
        memo_offset = self._next_pattern_ptr
        pattern_offset = memo_offset - len(memo_bytes)

        # Sanity check: verify our size computations match.
        assert len(pat_bytes) == bytes_per_pattern(stitches, rows)
        assert len(memo_bytes) == bytes_for_memo(rows)

        # --- Write into working region ---
        # memo block: last byte of memo_bytes sits at memo_offset.
        memo_start = memo_offset - len(memo_bytes) + 1
        self._data[memo_start : memo_offset + 1] = memo_bytes

        # pattern data: last byte sits at pattern_offset.
        pat_start = pattern_offset - len(pat_bytes) + 1
        self._data[pat_start : pattern_offset + 1] = pat_bytes

        # --- Write directory entry ---
        dir_offset, dir_bytes = encode_directory_entry(
            slot_index=self._next_slot,
            number=number,
            stitches=stitches,
            rows=rows,
            memo_offset=memo_offset,
            data_length=WORKING_REGION_SIZE,
        )
        self._data[dir_offset : dir_offset + DIRECTORY_ENTRY_SIZE] = dir_bytes

        # --- Advance cursors ---
        self._next_pattern_ptr -= total
        self._next_slot += 1

        # Decode the entry we just wrote and return it for confirmation.
        return decode_directory_entry(
            self._data[dir_offset : dir_offset + DIRECTORY_ENTRY_SIZE]
        )  # type: ignore[return-value]

    # ---------------------------------------------------------------------------
    # Serialisation
    # ---------------------------------------------------------------------------

    def working_region_bytes(self) -> bytes:
        """Return the 2,048-byte working region as an immutable bytes object."""
        return bytes(self._data)

    def to_sector_files(self) -> dict[int, bytes]:
        """
        Return the full disk image as a dict mapping sector number (0–79) to
        1,024-byte sector data.  Sectors 0 and 1 contain the working region;
        sectors 2–79 are zero-padded.

        This is what PDDemulate expects: sector N → file ``NN.dat``.
        """
        sectors: dict[int, bytes] = {}
        working = bytes(self._data)
        sectors[0] = working[:SECTOR_SIZE]
        sectors[1] = working[SECTOR_SIZE:WORKING_REGION_SIZE]
        for n in range(2, NUM_SECTORS):
            sectors[n] = bytes(SECTOR_SIZE)
        return sectors

    def to_disk_image_bytes(self) -> bytes:
        """
        Return the full 81,920-byte disk image as a single bytes object.
        Sectors beyond the working region are zero-padded.
        """
        sectors = self.to_sector_files()
        return b"".join(sectors[n] for n in range(NUM_SECTORS))

    # ---------------------------------------------------------------------------
    # Convenience
    # ---------------------------------------------------------------------------

    def __repr__(self) -> str:
        patterns = self.list_patterns()
        nums = [e.number for e in patterns]
        return (
            f"DiskImage(patterns={nums}, "
            f"slots_used={self._next_slot}/{MAX_PATTERNS}, "
            f"bytes_remaining={self._next_pattern_ptr})"
        )
