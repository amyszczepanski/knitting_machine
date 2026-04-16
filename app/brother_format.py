"""
brother_format.py — Brother KH-930/940 disk image format encoder/decoder.

Binary format references:
  KH-930: reverse-engineered by Steve Conklin, Travis Goodspeed, and others;
          documented in https://github.com/stg/knittington/blob/master/doc/kh940_format.txt
  KH-940: documented in the knittington KH940 format spec

============================================================
KH-930 DISK IMAGE LAYOUT
============================================================
A full disk image is 81,920 bytes: 80 sectors × 1,024 bytes each.
The machine treats this as a flat 2,048-byte address space (the first two
sectors, 00.dat and 01.dat, are the only ones that matter for pattern data).

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

PATTERN DIRECTORY ENTRY — KH-930 (7 bytes per pattern, 99 entries, index 0–98)
-------------------------------------------------------------------------------
Each entry uses BCD (Binary Coded Decimal) encoding: each decimal digit
occupies one nibble (4 bits).

  Byte 0:   flag      — 0x00 = empty slot; nonzero = valid pattern.
                        Also the high byte of a 16-bit reversed-address pointer.
  Byte 1:   unknown   — Low byte of the reversed-address pointer.
  Byte 2:   rh:rt     — MSN=rows hundreds, LSN=rows tens
  Byte 3:   ro:sh     — MSN=rows ones,    LSN=stitches hundreds
  Byte 4:   st:so     — MSN=stitches tens, LSN=stitches ones
  Byte 5:   unk:ph    — MSN=unused,        LSN=pattern number hundreds
  Byte 6:   pt:po     — MSN=pattern number tens, LSN=pattern number ones

  Pointer:  memo_offset = (WORKING_REGION_SIZE - 1) - ((flag << 8) | unknown)

============================================================
KH-940 DISK IMAGE LAYOUT
============================================================
A full disk image is 81,920 bytes: 80 sectors × 1,024 bytes each.
The machine uses the FIRST 32 sectors as a flat 32,768-byte working region
(0x0000–0x7FFF), treated as a binary dump of the machine's external RAM.

All offsets below are "reversed address" offsets from the LAST_BYTE (0x7FFF):
  file_address = 0x7FFF - offset
  offset = 0x7FFF - file_address

ADDRESS SPACE (file addresses within the 32,768-byte working region)
--------------------------------------------------------------------
0x0000–0x02AD   PATTERN_LIST: up to 98 pattern headers × 7 bytes each
0x02AE–0x7EDF   PATTERN_MEMORY: pattern data, grows upward toward 0x7EDF
0x7EE0–0x7EE6   AREA0 (unused, write 0x55)
0x7EE7–0x7EFF   AREA1 (write 0x00)
0x7F00–0x7F16   CONTROL_DATA (see below)
0x7F17–0x7F2F   AREA2 (write 0x00)
0x7F30–0x7FE9   AREA3 (write 0x00)
0x7FEA–0x7FEB   LOADED_PATTERN
0x7FEC–0x7FFE   AREA4 (write 0x00)
0x7FFF          LAST_BYTE (write 0x02)

PATTERN_LIST ENTRY — KH-940 (7 bytes per pattern, up to 98 entries)
--------------------------------------------------------------------
  Bytes 0–1: DATA_OFFSET  — Binary 16-bit unsigned reversed-address offset
                            pointing to the last byte of PatternMEMO.
                            Reversed address: file_addr = 0x7FFF - DATA_OFFSET
  Byte 2:    rh:rt        — MSN=rows hundreds, LSN=rows tens  (BCD)
  Byte 3:    ro:sh        — MSN=rows ones,     LSN=stitches hundreds  (BCD)
  Byte 4:    st:so        — MSN=stitches tens, LSN=stitches ones  (BCD)
  Byte 5:    0x0:ph       — MSN=always 0x0, LSN=pattern number hundreds  (BCD)
  Byte 6:    pt:po        — MSN=pattern number tens, LSN=pattern number ones  (BCD)

  Empty/unused slots are filled with 0x55.
  The entry immediately after the last valid pattern is a FINHDR sentinel:
    bytes 0–4 = 0x55, byte 5 = 0x0N (ph), byte 6 = pt:po
    where NNN = next unused pattern number.

CONTROL_DATA block (file addresses 0x7F00–0x7F16)
--------------------------------------------------
  +0x00  2  PATTERN_PTR1  — offset of (first byte of last pattern + 1)
  +0x02  2  UNK1          — write 0x0001
  +0x04  2  PATTERN_PTR0  — same as PATTERN_PTR1
  +0x06  2  LAST_BOTTOM   — offset to last byte of last created pattern
  +0x08  2  UNK2          — write 0x0000
  +0x0A  2  LAST_TOP      — offset to first byte of last created pattern
  +0x0C  4  UNK3          — write 0x00008100
  +0x10  2  HEADER_PTR    — offset to end of pattern header list (= 0x7FF9 after format)
  +0x12  2  UNK_PTR       — write 0x0000
  +0x14  3  UNK4          — write 0x000000

LOADED_PATTERN (file address 0x7FEA, 2 bytes)
----------------------------------------------
  Byte 0: 0x1N where N = pattern number hundreds (BCD)
  Byte 1: pt:po pattern number tens:ones (BCD)
  Write as last created pattern number; after format write 0x1000.

PATTERN DATA LAYOUT (same for both machines)
--------------------------------------------
For each valid pattern, layout within the working region (high to low address):

  [PatternMEMO]  bytes_for_memo(rows) bytes     — at memo_offset, grows down
  [PatternDATA]  bytes_per_pattern(stitches, rows) bytes — immediately below

NIBBLE AND BYTE GEOMETRY (same for both machines)
-------------------------------------------------
  nibblesPerRow(stitches) = ceil4(stitches) / 4
  bytesPerPattern(stitches, rows) = ceil2(rows × nibblesPerRow(stitches)) / 2
  bytesForMemo(rows) = ceil2(rows) / 2

NIBBLE ADDRESSING (same for both machines)
------------------------------------------
Nibbles are numbered 0, 1, 2, ... starting from base and advancing BACKWARD:
  nibble N lives in byte at address:  base - (N // 2)
  even N → LSN (bits 3:0);  odd N → MSN (bits 7:4)

STITCH BIT ORDER WITHIN A NIBBLE (same for both machines)
----------------------------------------------------------
  bit 0 (LSB) → stitch 0 (leftmost); bit 3 (MSB) → stitch 3 (rightmost)
  stitch value 1 = needle selected (knit); 0 = not selected (skip).

ROW ORDER (same for both machines)
-----------------------------------
Row 0 is the FIRST row knitted. Rows stored sequentially in nibble-space.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Sequence

# ---------------------------------------------------------------------------
# Machine model
# ---------------------------------------------------------------------------


class MachineModel(Enum):
    """Supported Brother knitting machine models."""

    KH930 = "KH-930"
    KH940 = "KH-940"


# ---------------------------------------------------------------------------
# Constants — shared
# ---------------------------------------------------------------------------

SECTOR_SIZE: int = 1024
NUM_SECTORS: int = 80
DISK_IMAGE_SIZE: int = NUM_SECTORS * SECTOR_SIZE  # 81,920 bytes

PATTERN_NUMBER_MIN: int = 901
PATTERN_NUMBER_MAX: int = 999

DIRECTORY_ENTRY_SIZE: int = 7

# ---------------------------------------------------------------------------
# Constants — KH-930
# ---------------------------------------------------------------------------

KH930_WORKING_REGION_SIZE: int = 2 * SECTOR_SIZE  # 2,048 bytes
KH930_WORKING_SECTORS: int = 2
KH930_MAX_PATTERNS: int = 99
KH930_INIT_PATTERN_OFFSET: int = 0x06DF
KH930_CURRENT_PATTERN_ADDR: int = 0x07EA
KH930_CURRENT_ROW_ADDR: int = 0x06FF
KH930_NEXT_ROW_ADDR: int = 0x072F
KH930_CURRENT_ROW_NUMBER_ADDR: int = 0x0702
KH930_CARRIAGE_STATUS_ADDR: int = 0x070F
KH930_SELECT_ADDR: int = 0x07EA
KH930_MAX_ROWS: int = 41

# ---------------------------------------------------------------------------
# Constants — KH-940
# ---------------------------------------------------------------------------

KH940_WORKING_REGION_SIZE: int = 32 * SECTOR_SIZE  # 32,768 bytes
KH940_WORKING_SECTORS: int = 32
KH940_MAX_PATTERNS: int = 98
KH940_INIT_PATTERN_OFFSET: int = 0x7EDF  # file address
KH940_LAST_BYTE_ADDR: int = 0x7FFF
KH940_MAX_ROWS: int = 999  # limited by interface rather than memory

# Control data block base address (file address)
KH940_CONTROL_DATA_ADDR: int = 0x7F00
KH940_LOADED_PATTERN_ADDR: int = 0x7FEA

# Reversed-address base: offset = KH940_LAST_BYTE_ADDR - file_address
KH940_REVERSED_BASE: int = KH940_LAST_BYTE_ADDR

# The FINHDR sentinel and unused slots are filled with this value.
KH940_FILL_BYTE: int = 0x55

# ---------------------------------------------------------------------------
# Backwards-compatibility aliases (used by existing tests and api.py)
# These reflect the KH-930 values that the original code used.
# ---------------------------------------------------------------------------

WORKING_REGION_SIZE: int = KH930_WORKING_REGION_SIZE
MAX_PATTERNS: int = KH930_MAX_PATTERNS
INIT_PATTERN_OFFSET: int = KH930_INIT_PATTERN_OFFSET
CURRENT_PATTERN_ADDR: int = KH930_CURRENT_PATTERN_ADDR
CURRENT_ROW_ADDR: int = KH930_CURRENT_ROW_ADDR
NEXT_ROW_ADDR: int = KH930_NEXT_ROW_ADDR
CURRENT_ROW_NUMBER_ADDR: int = KH930_CURRENT_ROW_NUMBER_ADDR
CARRIAGE_STATUS_ADDR: int = KH930_CARRIAGE_STATUS_ADDR
SELECT_ADDR: int = KH930_SELECT_ADDR


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

    The returned bytearray uses backward nibble addressing: the first row's
    nibble 0 is in the LSN of the last byte.  Callers should place this block
    so that its last byte sits at pattern_offset in the working region and
    grows toward lower addresses.
    """
    if len(pixel_rows) != rows:
        raise ValueError(f"Expected {rows} rows, got {len(pixel_rows)}")
    npr = nibbles_per_row(stitches)
    total_nibbles = rows * npr
    total_bytes = _ceil2(total_nibbles) // 2

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
# Directory entry encode / decode — KH-930
# ---------------------------------------------------------------------------


@dataclass
class PatternEntry:
    """Metadata for one pattern stored in the Brother directory.

    Compatible with both KH-930 and KH-940; the pointer fields are stored
    differently on each machine but the computed properties are the same.
    """

    number: int  # 901–999
    stitches: int  # 1–200
    rows: int  # 1–999

    # KH-930: flag = high byte of reversed-address pointer;
    #         pointer_low = low byte.
    # KH-940: flag = high byte of 16-bit DATA_OFFSET;
    #         pointer_low = low byte.
    # In both cases memo_offset is computed from these two bytes.
    flag: int
    pointer_low: int

    # The working region size for this entry's machine (needed for the
    # reversed-address calculation).
    _working_region_size: int = field(default=KH930_WORKING_REGION_SIZE, repr=False)

    @property
    def memo_offset(self) -> int:
        """
        Absolute file address of the memo block's base byte.
        Computed from the reversed-address pointer:
          memo_offset = (working_region_size - 1) - ((flag << 8) | pointer_low)
        """
        return (self._working_region_size - 1) - ((self.flag << 8) | self.pointer_low)

    @property
    def pattern_offset(self) -> int:
        """Absolute file address of the pattern data's base byte (just below memo)."""
        return self.memo_offset - bytes_for_memo(self.rows)

    @property
    def block_end_offset(self) -> int:
        """
        Absolute file address of the byte just below the entire pattern+memo block.
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
    Encode one 7-byte KH-930 directory entry.

    `slot_index` is 0-based (0 = first pattern slot, bytes 0–6 of the file).
    `memo_offset` is the absolute byte offset of this pattern's memo base.
    `data_length` is len(working_region) = WORKING_REGION_SIZE (KH-930: 2,048).

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
    Decode a 7-byte KH-930 directory entry.
    Returns None if the slot is empty (flag == 0).
    """
    if len(raw) < DIRECTORY_ENTRY_SIZE:
        raise ValueError(
            f"Directory entry must be {DIRECTORY_ENTRY_SIZE} bytes, got {len(raw)}"
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
        _working_region_size=KH930_WORKING_REGION_SIZE,
    )


# ---------------------------------------------------------------------------
# Directory entry encode / decode — KH-940
# ---------------------------------------------------------------------------


def encode_directory_entry_940(
    slot_index: int,
    number: int,
    stitches: int,
    rows: int,
    memo_offset: int,
) -> tuple[int, bytes]:
    """
    Encode one 7-byte KH-940 pattern list entry.

    `memo_offset` is the absolute file address of this pattern's memo base.
    DATA_OFFSET field = KH940_REVERSED_BASE - memo_offset (16-bit binary).

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

    data_offset = KH940_REVERSED_BASE - memo_offset
    flag = (data_offset >> 8) & 0xFF
    ptr_low = data_offset & 0xFF

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
            (0x0 << 4) | ph,  # upper nibble always 0
            (pt << 4) | po,
        ]
    )

    byte_offset = slot_index * DIRECTORY_ENTRY_SIZE
    return byte_offset, entry


def decode_directory_entry_940(raw: bytes | bytearray) -> PatternEntry | None:
    """
    Decode a 7-byte KH-940 pattern list entry.

    Returns None if the slot is empty (first byte == KH940_FILL_BYTE == 0x55)
    or if this looks like the FINHDR sentinel (bytes 0–4 are all 0x55).
    """
    if len(raw) < DIRECTORY_ENTRY_SIZE:
        raise ValueError(
            f"Directory entry must be {DIRECTORY_ENTRY_SIZE} bytes, got {len(raw)}"
        )

    # Empty / FINHDR / unused: first byte is 0x55
    if raw[0] == KH940_FILL_BYTE:
        return None

    flag = raw[0]
    ptr_low = raw[1]
    rh = (raw[2] & 0xF0) >> 4
    rt = raw[2] & 0x0F
    ro = (raw[3] & 0xF0) >> 4
    sh = raw[3] & 0x0F
    st = (raw[4] & 0xF0) >> 4
    so = raw[4] & 0x0F
    ph = raw[5] & 0x0F  # upper nibble always 0
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
        _working_region_size=KH940_WORKING_REGION_SIZE,
    )


def _encode_finhdr_940(slot_index: int, next_number: int) -> tuple[int, bytes]:
    """
    Encode the FINHDR sentinel entry for the KH-940 pattern list.

    The FINHDR marks the end of the pattern list.  Bytes 0–4 = 0x55;
    bytes 5–6 encode the next unused pattern number in BCD.
    """
    ph, pt, po = _bcd_encode_3digit(next_number)
    entry = bytes(
        [
            KH940_FILL_BYTE,  # 0
            KH940_FILL_BYTE,  # 1
            KH940_FILL_BYTE,  # 2
            KH940_FILL_BYTE,  # 3
            KH940_FILL_BYTE,  # 4
            (0x0 << 4) | ph,  # 5  XX=0, hundreds
            (pt << 4) | po,  # 6  tens, ones
        ]
    )
    return slot_index * DIRECTORY_ENTRY_SIZE, entry


# ---------------------------------------------------------------------------
# DiskImage — top-level object, supports both KH-930 and KH-940
# ---------------------------------------------------------------------------


@dataclass
class DiskImage:
    """
    An in-memory representation of a Brother knitting machine disk image.

    Supports both the KH-930 (2 KB working region) and KH-940 (32 KB working
    region) formats.  The default model is KH-940.

    The full disk image is always DISK_IMAGE_SIZE (81,920) bytes when
    serialised; the working region occupies the first N sectors.

    Usage::

        img = DiskImage.blank()                   # KH-940 (default)
        img = DiskImage.blank(MachineModel.KH930) # KH-930
        img.write_pattern(901, pixel_rows)
        raw = img.to_disk_image_bytes()

    """

    model: MachineModel = field(default=MachineModel.KH940)

    # Full working region as a mutable bytearray.
    _data: bytearray = field(init=False)

    # Next available file address for pattern data (starts at INIT_PATTERN_OFFSET,
    # decrements as patterns are added).
    _next_pattern_ptr: int = field(init=False)

    # Slot index for the next directory entry (0-based).
    _next_slot: int = field(init=False)

    def __post_init__(self) -> None:
        size = self._working_region_size
        if self.model == MachineModel.KH940:
            # KH-940: fill unused areas with 0x55 per spec, then zero out
            # the areas that must be 0x00.
            self._data = bytearray([KH940_FILL_BYTE] * size)
            self._zero_940_regions()
        else:
            self._data = bytearray(size)
        self._next_pattern_ptr = self._init_pattern_offset
        self._next_slot = 0

    # ------------------------------------------------------------------
    # Properties derived from model
    # ------------------------------------------------------------------

    @property
    def max_rows(self) -> int:
        return KH940_MAX_ROWS if self.model == MachineModel.KH940 else KH930_MAX_ROWS

    @property
    def _working_region_size(self) -> int:
        return (
            KH940_WORKING_REGION_SIZE
            if self.model == MachineModel.KH940
            else KH930_WORKING_REGION_SIZE
        )

    @property
    def _working_sectors(self) -> int:
        return (
            KH940_WORKING_SECTORS
            if self.model == MachineModel.KH940
            else KH930_WORKING_SECTORS
        )

    @property
    def _max_patterns(self) -> int:
        return (
            KH940_MAX_PATTERNS
            if self.model == MachineModel.KH940
            else KH930_MAX_PATTERNS
        )

    @property
    def _init_pattern_offset(self) -> int:
        return (
            KH940_INIT_PATTERN_OFFSET
            if self.model == MachineModel.KH940
            else KH930_INIT_PATTERN_OFFSET
        )

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    @classmethod
    def blank(cls, model: MachineModel = MachineModel.KH940) -> "DiskImage":
        """
        Create a blank disk image with an empty pattern directory.

        Parameters
        ----------
        model:
            The target machine model.  Defaults to KH-940.
        """
        return cls(model=model)

    @classmethod
    def from_bytes(
        cls,
        data: bytes | bytearray,
        model: MachineModel = MachineModel.KH940,
    ) -> "DiskImage":
        """
        Load a DiskImage from an existing working-region blob or full disk image.

        Parameters
        ----------
        data:
            Working-region bytes (≥ working_region_size) or full disk image
            (81,920 bytes).  Only the first working_region_size bytes are used.
        model:
            The target machine model.  Defaults to KH-940.
        """
        size = (
            KH940_WORKING_REGION_SIZE
            if model == MachineModel.KH940
            else KH930_WORKING_REGION_SIZE
        )
        if len(data) < size:
            raise ValueError(
                f"Data too short for {model.value}: need at least {size} bytes, "
                f"got {len(data)}"
            )
        img = cls(model=model)
        img._data = bytearray(data[:size])
        img._sync_state_from_directory()
        return img

    def _zero_940_regions(self) -> None:
        """Zero out the KH-940 regions that must be 0x00 (not 0x55)."""
        # PATTERN_LIST directory area: 0x0000–0x02AD (filled with 0x55 is
        # correct for unused slots; we zero only the structural fields lazily
        # as patterns are written).  The fill byte for the directory is 0x55,
        # which __post_init__ already set, so nothing to do here.

        # AREA1: 0x7EE7–0x7EFF — write 0x00
        self._data[0x7EE7:0x7F00] = bytes(0x7F00 - 0x7EE7)

        # CONTROL_DATA: 0x7F00–0x7F16 — initialise fully below.
        # Per the spec, all pointer fields are 0x0000 on a freshly-formatted
        # disk (no patterns yet).  We pass sentinel value 0 for last_number
        # so that LOADED_PATTERN is left at its separately-written 0x1000
        # default; _write_940_control_data skips the LOADED_PATTERN write
        # when last_number is 0.
        self._write_940_control_data_blank()

        # AREA2: 0x7F17–0x7F2F — write 0x00
        self._data[0x7F17:0x7F30] = bytes(0x7F30 - 0x7F17)

        # AREA3: 0x7F30–0x7FE9 — write 0x00
        self._data[0x7F30:0x7FEA] = bytes(0x7FEA - 0x7F30)

        # LOADED_PATTERN: 0x7FEA–0x7FEB — default after format = 0x1000
        self._data[0x7FEA] = 0x10
        self._data[0x7FEB] = 0x00

        # AREA4: 0x7FEC–0x7FFE — write 0x00
        self._data[0x7FEC:0x7FFF] = bytes(0x7FFF - 0x7FEC)

        # LAST_BYTE: 0x7FFF = 0x02
        self._data[0x7FFF] = 0x02

    def _write_940_control_data_blank(self) -> None:
        """
        Write the KH-940 CONTROL_DATA block for a freshly-formatted disk
        (no patterns).  Per the spec all pointer fields are 0x0000; the
        fixed UNK and structural fields are written normally.
        LOADED_PATTERN is NOT touched here — it is set to 0x1000 by the
        caller (_zero_940_regions) immediately after this call.
        """
        base = KH940_CONTROL_DATA_ADDR

        def _write16(offset: int, value: int) -> None:
            self._data[base + offset] = (value >> 8) & 0xFF
            self._data[base + offset + 1] = value & 0xFF

        _write16(0x00, 0x0000)  # PATTERN_PTR1 — 0x0000 after format
        _write16(0x02, 0x0001)  # UNK1         — fixed value, same as non-blank
        _write16(0x04, 0x0000)  # PATTERN_PTR0 — 0x0000 after format
        _write16(0x06, 0x0000)  # LAST_BOTTOM  — 0x0000 after format
        _write16(0x08, 0x0000)  # UNK2
        _write16(0x0A, 0x0000)  # LAST_TOP     — 0x0000 after format
        # UNK3: 4 bytes = 0x00000000 after format
        self._data[base + 0x0C] = 0x00
        self._data[base + 0x0D] = 0x00
        self._data[base + 0x0E] = 0x00
        self._data[base + 0x0F] = 0x00
        _write16(0x10, 0x7FF9)  # HEADER_PTR   — 0x7FF9 after format
        _write16(0x12, 0x0000)  # UNK_PTR
        self._data[base + 0x14] = 0x00
        self._data[base + 0x15] = 0x00
        self._data[base + 0x16] = 0x00  # UNK4

    def _write_940_control_data(
        self,
        next_ptr: int,
        last_bottom: int,
        last_top: int,
        last_number: int,
    ) -> None:
        """
        Write the KH-940 CONTROL_DATA block at file address 0x7F00.

        Parameters
        ----------
        next_ptr:
            Reversed-address offset of (first byte of last pattern + 1).
            = KH940_REVERSED_BASE - first_byte_of_last_pattern + 1
        last_bottom:
            Reversed-address offset of the last byte of the last pattern
            (= the memo block's last byte = memo_offset).
            = KH940_REVERSED_BASE - memo_offset_of_last_pattern
        last_top:
            Reversed-address offset of the first byte of the last pattern's
            DATA block (not the memo, and not the combined block start).
            = KH940_REVERSED_BASE - pattern_offset_of_last_pattern
        last_number:
            Pattern number of the last created pattern (901–999).
        """
        base = KH940_CONTROL_DATA_ADDR

        def _write16(offset: int, value: int) -> None:
            self._data[base + offset] = (value >> 8) & 0xFF
            self._data[base + offset + 1] = value & 0xFF

        _write16(0x00, next_ptr)  # PATTERN_PTR1
        _write16(0x02, 0x0001)  # UNK1
        _write16(0x04, next_ptr)  # PATTERN_PTR0
        _write16(0x06, last_bottom)  # LAST_BOTTOM
        _write16(0x08, 0x0000)  # UNK2
        _write16(0x0A, last_top)  # LAST_TOP
        # UNK3: 4 bytes = 0x00008100
        self._data[base + 0x0C] = 0x00
        self._data[base + 0x0D] = 0x00
        self._data[base + 0x0E] = 0x81
        self._data[base + 0x0F] = 0x00
        _write16(0x10, 0x7FF9)  # HEADER_PTR (default; updated after writes)
        _write16(0x12, 0x0000)  # UNK_PTR
        self._data[base + 0x14] = 0x00
        self._data[base + 0x15] = 0x00
        self._data[base + 0x16] = 0x00  # UNK4

        # LOADED_PATTERN at 0x7FEA
        ph, pt, po = _bcd_encode_3digit(last_number)
        self._data[KH940_LOADED_PATTERN_ADDR] = (0x1 << 4) | ph
        self._data[KH940_LOADED_PATTERN_ADDR + 1] = (pt << 4) | po

    def _update_940_metadata(self) -> None:
        """
        Refresh the KH-940 CONTROL_DATA and LOADED_PATTERN after a write.
        Called at the end of write_pattern() for KH-940.
        """
        entries = self.list_patterns()
        if not entries:
            return
        last = entries[-1]

        # LAST_BOTTOM = reversed offset of memo_offset (last byte of the
        #               memo block, which is the last byte of the whole entry).
        memo_rev = KH940_REVERSED_BASE - last.memo_offset

        # LAST_TOP = reversed offset of the first byte of the pattern DATA
        #            block (not the memo, not the combined start).
        #            pattern_offset is the *last* byte of the DATA block, so
        #            the first byte is at:
        #              pat_first = pattern_offset - bytes_per_pattern(...) + 1
        pat_data_bytes = bytes_per_pattern(last.stitches, last.rows)
        pat_first = last.pattern_offset - pat_data_bytes + 1
        pat_rev = KH940_REVERSED_BASE - pat_first

        # PATTERN_PTR1/PTR0 = reversed offset of (first byte of last pattern + 1)
        # = reversed offset of the byte *just above* the DATA block.
        # Since reversed addresses decrease as file addresses increase, adding 1
        # to a reversed offset means one byte *closer* to LAST_BYTE (lower file addr).
        # Equivalently: KH940_REVERSED_BASE - (pat_first - 1)
        next_ptr = KH940_REVERSED_BASE - (pat_first - 1)

        # HEADER_PTR = reversed offset of the first byte of the FINHDR entry,
        # i.e. the byte immediately after the last valid directory slot.
        # self._next_slot has already been incremented to include the pattern
        # we just wrote, so (self._next_slot * 7) is the file address of the
        # first byte of the FINHDR.
        header_ptr = KH940_REVERSED_BASE - (self._next_slot * DIRECTORY_ENTRY_SIZE)

        self._write_940_control_data(
            next_ptr=next_ptr,
            last_bottom=memo_rev,
            last_top=pat_rev,
            last_number=last.number,
        )

        # Write HEADER_PTR separately (it differs from defaults)
        base = KH940_CONTROL_DATA_ADDR
        self._data[base + 0x10] = (header_ptr >> 8) & 0xFF
        self._data[base + 0x11] = header_ptr & 0xFF

    def _sync_state_from_directory(self) -> None:
        """
        After loading from bytes, scan the directory to find the current
        _next_slot and _next_pattern_ptr so that new patterns can be appended.
        """
        decode_fn = (
            decode_directory_entry_940
            if self.model == MachineModel.KH940
            else decode_directory_entry
        )
        last_entry = None
        for slot in range(self._max_patterns):
            raw = self._data[
                slot * DIRECTORY_ENTRY_SIZE : (slot + 1) * DIRECTORY_ENTRY_SIZE
            ]
            entry = decode_fn(raw)
            if entry is None:
                self._next_slot = slot
                break
            last_entry = entry
            self._next_pattern_ptr = entry.block_end_offset
        else:
            # All slots occupied: _next_pattern_ptr was set on the last
            # iteration above, so it is already correct.  Just fix _next_slot.
            self._next_slot = self._max_patterns

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------

    def list_patterns(self) -> list[PatternEntry]:
        """Return a list of all valid PatternEntry objects in the directory."""
        decode_fn = (
            decode_directory_entry_940
            if self.model == MachineModel.KH940
            else decode_directory_entry
        )
        entries: list[PatternEntry] = []
        for slot in range(self._max_patterns):
            raw = self._data[
                slot * DIRECTORY_ENTRY_SIZE : (slot + 1) * DIRECTORY_ENTRY_SIZE
            ]
            entry = decode_fn(raw)
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

    # ------------------------------------------------------------------
    # Writing
    # ------------------------------------------------------------------

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
        if self._next_slot >= self._max_patterns:
            raise ValueError(
                f"Disk image is full ({self._max_patterns} patterns already stored)"
            )
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
        if self.model == MachineModel.KH940:
            dir_offset, dir_bytes = encode_directory_entry_940(
                slot_index=self._next_slot,
                number=number,
                stitches=stitches,
                rows=rows,
                memo_offset=memo_offset,
            )
        else:
            dir_offset, dir_bytes = encode_directory_entry(
                slot_index=self._next_slot,
                number=number,
                stitches=stitches,
                rows=rows,
                memo_offset=memo_offset,
                data_length=self._working_region_size,
            )
        self._data[dir_offset : dir_offset + DIRECTORY_ENTRY_SIZE] = dir_bytes

        # --- Advance cursors ---
        self._next_pattern_ptr -= total
        self._next_slot += 1

        # --- KH-940: update FINHDR and control/metadata blocks ---
        if self.model == MachineModel.KH940:
            next_number = min(number + 1, PATTERN_NUMBER_MAX)
            finhdr_offset, finhdr_bytes = _encode_finhdr_940(
                self._next_slot, next_number
            )
            self._data[finhdr_offset : finhdr_offset + DIRECTORY_ENTRY_SIZE] = (
                finhdr_bytes
            )
            self._update_940_metadata()

        # Decode the entry we just wrote and return it for confirmation.
        if self.model == MachineModel.KH940:
            return decode_directory_entry_940(
                self._data[dir_offset : dir_offset + DIRECTORY_ENTRY_SIZE]
            )  # type: ignore[return-value]
        else:
            return decode_directory_entry(
                self._data[dir_offset : dir_offset + DIRECTORY_ENTRY_SIZE]
            )  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def working_region_bytes(self) -> bytes:
        """Return the working region as an immutable bytes object."""
        return bytes(self._data)

    def to_sector_files(self) -> dict[int, bytes]:
        """
        Return the full disk image as a dict mapping sector number (0–79) to
        1,024-byte sector data.  The first _working_sectors sectors contain the
        working region; the remainder are zero-padded.

        This is what PDDEmulator expects: sector N → file ``NN.dat``.
        """
        sectors: dict[int, bytes] = {}
        working = bytes(self._data)
        for n in range(self._working_sectors):
            sectors[n] = working[n * SECTOR_SIZE : (n + 1) * SECTOR_SIZE]
        for n in range(self._working_sectors, NUM_SECTORS):
            sectors[n] = bytes(SECTOR_SIZE)
        return sectors

    def to_disk_image_bytes(self) -> bytes:
        """
        Return the full 81,920-byte disk image as a single bytes object.
        Sectors beyond the working region are zero-padded.
        """
        sectors = self.to_sector_files()
        return b"".join(sectors[n] for n in range(NUM_SECTORS))

    # ------------------------------------------------------------------
    # Convenience
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        patterns = self.list_patterns()
        nums = [e.number for e in patterns]
        return (
            f"DiskImage(model={self.model.value}, patterns={nums}, "
            f"slots_used={self._next_slot}/{self._max_patterns}, "
            f"bytes_remaining={self._next_pattern_ptr})"
        )
