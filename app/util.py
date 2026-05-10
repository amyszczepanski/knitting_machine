"""
util.py - Helper functions that don't get into the nitty gritty of the format
"""

# ---------------------------------------------------------------------------
# Low-level geometry helpers
# ---------------------------------------------------------------------------


def ceil4(n: int) -> int:
    """Round n up to the nearest multiple of 4."""
    r = n % 4
    return n if r == 0 else n + (4 - r)


def ceil2(n: int) -> int:
    """Round n up to the nearest multiple of 2 (i.e. make even)."""
    return n if n % 2 == 0 else n + 1


def nibbles_per_row(stitches: int) -> int:
    """
    Number of nibbles required to store one row of `stitches` stitches.
    Stitch count is rounded up to the nearest multiple of 4 (nibble-aligned).
    """
    return ceil4(stitches) // 4


def bytes_per_pattern(stitches: int, rows: int) -> int:
    """
    Total bytes required to store pattern pixel data (not including memo).
    """
    nibbles = rows * nibbles_per_row(stitches)
    return ceil2(nibbles) // 2


def bytes_for_memo(rows: int) -> int:
    """
    Total bytes required for the memo block (1 nibble per row, byte-aligned).
    """
    return ceil2(rows) // 2


def bytes_per_pattern_and_memo(stitches: int, rows: int) -> int:
    """Total bytes consumed by one pattern entry (data + memo)."""
    return bytes_per_pattern(stitches, rows) + bytes_for_memo(rows)


# ---------------------------------------------------------------------------
# BCD helpers
# ---------------------------------------------------------------------------


def bcd_encode_3digit(value: int) -> tuple[int, int, int]:
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


def bcd_decode_3digit(hundreds: int, tens: int, ones: int) -> int:
    """Decode three BCD nibbles back to an integer."""
    return 100 * hundreds + 10 * tens + ones


# ---------------------------------------------------------------------------
# Nibble-level read/write (backward addressing)
# ---------------------------------------------------------------------------


def read_nibble(data: bytearray | bytes, base: int, nibble_index: int) -> int:
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


def write_nibble(data: bytearray, base: int, nibble_index: int, value: int) -> None:
    """
    Write a single nibble (0–15) into `data` using the same backward addressing
    as read_nibble.  Only the target nibble is modified; the other nibble in
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
