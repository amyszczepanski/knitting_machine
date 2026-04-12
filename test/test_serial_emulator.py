"""
test_serial_emulator.py — pytest suite for serial_emulator.py

Run with:
    pytest test_serial_emulator.py -v

pyserial is mocked so these tests run without a serial port installed.
"""

import io
import sys
import types
from pathlib import Path

import pytest

from app import serial_emulator as se
from app import brother_format as bf

# ---------------------------------------------------------------------------
# Mock pyserial before importing the module under test
# ---------------------------------------------------------------------------

_serial_mock = types.ModuleType("serial")
_serial_mock.PARITY_NONE = "N"
_serial_mock.STOPBITS_ONE = 1
_serial_mock.EIGHTBITS = 8


class _FakeSerial:
    def __init__(self, **kw):
        self.in_waiting = 0

    def read(self, n=1):
        return b""

    def write(self, b):
        pass

    def close(self):
        pass


_serial_mock.Serial = _FakeSerial
sys.modules["serial"] = _serial_mock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakePort:
    """Simulates a serial port: feeds pre-loaded bytes as input, captures output."""

    def __init__(self, rx: bytes):
        self._rx = io.BytesIO(rx)
        self._tx = io.BytesIO()
        self.in_waiting = len(rx)

    def read(self, n: int = 1) -> bytes:
        return self._rx.read(n)

    def write(self, b: bytes) -> None:
        self._tx.write(b)

    def sent(self) -> bytes:
        return self._tx.getvalue()


def _make_emulator(disk_dir: Path) -> se.PDDEmulator:
    emu = se.PDDEmulator(disk_dir)
    emu._fdc_mode = True
    return emu


# ---------------------------------------------------------------------------
# _status_ok helper
# ---------------------------------------------------------------------------


class TestStatusOk:
    @pytest.mark.parametrize(
        "psn,expected",
        [
            (0, b"00000000"),
            (1, b"00010000"),
            (5, b"00050000"),
            (15, b"000F0000"),
            (16, b"00100000"),
            (79, b"004F0000"),
        ],
    )
    def test_known_values(self, psn, expected):
        assert se._status_ok(psn) == expected

    def test_is_uppercase_ascii(self):
        result = se._status_ok(10)
        assert result == result.upper()
        assert len(result) == 8


# ---------------------------------------------------------------------------
# _VirtualDisk
# ---------------------------------------------------------------------------


class TestVirtualDisk:
    def test_blank_sector_is_zeros(self, tmp_path):
        d = se._VirtualDisk(tmp_path)
        assert d.read_sector(0) == bytes(se.SECTOR_SIZE)

    def test_blank_id_is_zeros(self, tmp_path):
        d = se._VirtualDisk(tmp_path)
        assert d.read_id(0) == bytes(se.ID_SIZE)

    def test_write_and_read_sector(self, tmp_path):
        d = se._VirtualDisk(tmp_path)
        payload = bytes(range(256)) * 4  # 1024 bytes
        d.write_sector(0, payload)
        assert d.read_sector(0) == payload

    def test_write_and_read_id(self, tmp_path):
        d = se._VirtualDisk(tmp_path)
        id_data = bytes(range(12))
        d.write_id(5, id_data)
        assert d.read_id(5) == id_data

    def test_even_sector_write_does_not_create_pair_file(self, tmp_path):
        d = se._VirtualDisk(tmp_path)
        d.write_sector(0, bytes(se.SECTOR_SIZE))
        assert d.last_written_pair is None

    def test_odd_sector_write_creates_pair_file(self, tmp_path):
        d = se._VirtualDisk(tmp_path)
        s0 = bytes([0xAA] * se.SECTOR_SIZE)
        s1 = bytes([0xBB] * se.SECTOR_SIZE)
        d.write_sector(0, s0)
        d.write_sector(1, s1)
        assert d.last_written_pair is not None
        assert d.last_written_pair.name == "file-01.dat"
        assert d.last_written_pair.read_bytes() == s0 + s1

    def test_pair_file_naming(self, tmp_path):
        d = se._VirtualDisk(tmp_path)
        data = bytes(se.SECTOR_SIZE)
        d.write_sector(0, data)
        d.write_sector(1, data)
        assert d.last_written_pair.name == "file-01.dat"
        d.write_sector(2, data)
        d.write_sector(3, data)
        assert d.last_written_pair.name == "file-02.dat"
        d.write_sector(78, data)
        d.write_sector(79, data)
        assert d.last_written_pair.name == "file-40.dat"

    def test_find_sector_by_id_match(self, tmp_path):
        d = se._VirtualDisk(tmp_path)
        target = bytes([0xAB] * 12)
        d.write_id(10, target)
        assert d.find_sector_by_id(0, target) == "000A0000"

    def test_find_sector_by_id_no_match(self, tmp_path):
        d = se._VirtualDisk(tmp_path)
        # All-0xFF won't match any default (zero) sector ID
        assert d.find_sector_by_id(0, bytes([0xFF] * 12)) == "40000000"

    def test_find_sector_by_id_starts_at_psn(self, tmp_path):
        d = se._VirtualDisk(tmp_path)
        target = bytes([0xCD] * 12)
        d.write_id(5, target)
        # Search starting after sector 5 should not find it
        assert d.find_sector_by_id(6, target) == "40000000"
        # Search from 0 or 5 should find it
        assert d.find_sector_by_id(0, target) == "00050000"
        assert d.find_sector_by_id(5, target) == "00050000"

    def test_format_zeros_all_sectors(self, tmp_path):
        d = se._VirtualDisk(tmp_path)
        payload = bytes(range(256)) * 4
        d.write_sector(0, payload)
        d.write_id(0, bytes(range(12)))
        d.format()
        assert d.read_sector(0) == bytes(se.SECTOR_SIZE)
        assert d.read_id(0) == bytes(se.ID_SIZE)

    def test_persistence_across_reload(self, tmp_path):
        d = se._VirtualDisk(tmp_path)
        payload = bytes(range(256)) * 4
        id_data = bytes(range(12))
        d.write_sector(7, payload)
        d.write_id(3, id_data)

        d2 = se._VirtualDisk(tmp_path)
        assert d2.read_sector(7) == payload
        assert d2.read_id(3) == id_data

    def test_wrong_size_sector_file_raises(self, tmp_path):
        bad = tmp_path / "00.dat"
        bad.write_bytes(bytes(512))  # wrong size
        with pytest.raises(ValueError):
            se._VirtualDisk(tmp_path)

    def test_wrong_size_id_file_raises(self, tmp_path):
        # Create a valid .dat first
        (tmp_path / "00.dat").write_bytes(bytes(se.SECTOR_SIZE))
        bad_id = tmp_path / "00.id"
        bad_id.write_bytes(bytes(6))  # wrong size
        with pytest.raises(ValueError):
            se._VirtualDisk(tmp_path)


# ---------------------------------------------------------------------------
# FDC command: R — read sector
# ---------------------------------------------------------------------------


class TestCmdReadSector:
    def test_returns_status_then_data(self, tmp_path):
        emu = _make_emulator(tmp_path)
        data = bytes([0x42] * se.SECTOR_SIZE)
        emu._disk.write_sector(0, data)
        port = _FakePort(b"0\r\r")
        emu._cmd_read_sector(se._SerialIO(port))
        sent = port.sent()
        assert sent[:8] == b"00000000"
        assert sent[8:] == data

    def test_correct_psn_in_status(self, tmp_path):
        emu = _make_emulator(tmp_path)
        port = _FakePort(b"15\r\r")
        emu._cmd_read_sector(se._SerialIO(port))
        assert port.sent()[:8] == b"000F0000"

    def test_no_data_sent_without_ack_cr(self, tmp_path):
        emu = _make_emulator(tmp_path)
        # No CR after the status — emulator should send status but no data
        port = _FakePort(b"0\r")  # params CR only; no ack CR
        emu._cmd_read_sector(se._SerialIO(port))
        assert len(port.sent()) == 8  # status only


# ---------------------------------------------------------------------------
# FDC command: W — write sector
# ---------------------------------------------------------------------------


class TestCmdWriteSector:
    def test_stores_data_and_returns_two_statuses(self, tmp_path):
        emu = _make_emulator(tmp_path)
        payload = bytes([0xDE, 0xAD] * 512)
        port = _FakePort(b"4\r" + payload)
        emu._cmd_write_sector(se._SerialIO(port))
        assert emu._disk.read_sector(4) == payload
        assert port.sent() == b"00040000" + b"00040000"

    def test_even_sector_no_callback(self, tmp_path):
        callbacks = []
        emu = _make_emulator(tmp_path)
        emu._on_write = callbacks.append
        port = _FakePort(b"2\r" + bytes(se.SECTOR_SIZE))
        emu._cmd_write_sector(se._SerialIO(port))
        assert callbacks == []

    def test_odd_sector_fires_callback(self, tmp_path):
        callbacks = []
        emu = _make_emulator(tmp_path)
        emu._on_write = callbacks.append
        emu._disk.write_sector(4, bytes(se.SECTOR_SIZE))  # even half
        port = _FakePort(b"5\r" + bytes(se.SECTOR_SIZE))
        emu._cmd_write_sector(se._SerialIO(port))
        assert len(callbacks) == 1
        assert callbacks[0].name == "file-03.dat"

    def test_callback_receives_correct_path(self, tmp_path):
        received = []
        emu = _make_emulator(tmp_path)
        emu._on_write = received.append
        data = bytes([0xAB] * se.SECTOR_SIZE)
        emu._disk.write_sector(0, data)
        port = _FakePort(b"1\r" + data)
        emu._cmd_write_sector(se._SerialIO(port))
        assert received[0].read_bytes() == data + data


# ---------------------------------------------------------------------------
# FDC command: A — read sector ID
# ---------------------------------------------------------------------------


class TestCmdReadId:
    def test_returns_status_then_id(self, tmp_path):
        emu = _make_emulator(tmp_path)
        id_val = bytes(range(12))
        emu._disk.write_id(3, id_val)
        port = _FakePort(b"3\r\r")
        emu._cmd_read_id(se._SerialIO(port))
        sent = port.sent()
        assert sent[:8] == b"00030000"
        assert sent[8:] == id_val

    def test_no_id_sent_without_ack_cr(self, tmp_path):
        emu = _make_emulator(tmp_path)
        port = _FakePort(b"0\r")  # no ack CR
        emu._cmd_read_id(se._SerialIO(port))
        assert len(port.sent()) == 8


# ---------------------------------------------------------------------------
# FDC command: B — write sector ID
# ---------------------------------------------------------------------------


class TestCmdWriteId:
    def test_stores_id_and_returns_two_statuses(self, tmp_path):
        emu = _make_emulator(tmp_path)
        new_id = bytes([0xFF] * 12)
        port = _FakePort(b"7\r" + new_id)
        emu._cmd_write_id(se._SerialIO(port))
        assert emu._disk.read_id(7) == new_id
        assert port.sent() == b"00070000" + b"00070000"


# ---------------------------------------------------------------------------
# FDC command: S — search sector by ID
# ---------------------------------------------------------------------------


class TestCmdSearchId:
    def test_match_returns_correct_sector(self, tmp_path):
        emu = _make_emulator(tmp_path)
        target = bytes([0xAB] * 12)
        emu._disk.write_id(11, target)
        port = _FakePort(b"0\r" + target)
        emu._cmd_search_id(se._SerialIO(port))
        sent = port.sent()
        assert sent[:8] == b"00000000"  # initial status at PSN=0
        assert sent[8:] == b"000B0000"  # match at sector 11 = 0x0B

    def test_no_match_returns_not_found(self, tmp_path):
        emu = _make_emulator(tmp_path)
        port = _FakePort(b"0\r" + bytes([0xFF] * 12))
        emu._cmd_search_id(se._SerialIO(port))
        sent = port.sent()
        assert sent[8:] == b"40000000"


# ---------------------------------------------------------------------------
# FDC command: F — format
# ---------------------------------------------------------------------------


class TestCmdFormat:
    def test_zeros_disk_and_returns_ok(self, tmp_path):
        emu = _make_emulator(tmp_path)
        emu._disk.write_sector(0, bytes([0xFF] * se.SECTOR_SIZE))
        port = _FakePort(b"5\r")
        emu._cmd_format(se._SerialIO(port))
        assert emu._disk.read_sector(0) == bytes(se.SECTOR_SIZE)
        assert port.sent() == b"00000000"

    def test_returns_to_opmode_after_format(self, tmp_path):
        emu = _make_emulator(tmp_path)
        emu._fdc_mode = True
        port = _FakePort(b"5\r")
        emu._cmd_format(se._SerialIO(port))
        assert emu._fdc_mode is False


# ---------------------------------------------------------------------------
# OpMode checksum
# ---------------------------------------------------------------------------


class TestOpModeChecksum:
    """
    Verify the checksum formula: ((req + len + sum(data)) % 256) ^ 0xFF
    The emulator uses this in _handle_opmode to validate incoming frames.
    """

    def _ck(self, req: int, data: bytes) -> int:
        return ((req + len(data) + sum(data)) % 256) ^ 0xFF

    def test_empty_data(self):
        # req=0x08, len=0: (0x08 + 0x00) % 256 ^ 0xFF = 0x08 ^ 0xFF = 0xF7
        assert self._ck(0x08, b"") == 0xF7

    def test_with_data_bytes(self):
        result = self._ck(0x08, bytes([1, 2, 3]))
        expected = ((0x08 + 3 + 1 + 2 + 3) % 256) ^ 0xFF
        assert result == expected

    def test_wraparound(self):
        # Force sum to exceed 255 to verify mod-256 wrapping
        data = bytes([0xFF] * 10)
        result = self._ck(0x08, data)
        expected = ((0x08 + 10 + 0xFF * 10) % 256) ^ 0xFF
        assert result == expected

    def test_result_is_single_byte(self):
        for req in range(0, 256, 17):
            for data_len in [0, 1, 5, 10]:
                data = bytes([req % 256] * data_len)
                assert 0 <= self._ck(req, data) <= 255


# ---------------------------------------------------------------------------
# load_disk_image integration
# ---------------------------------------------------------------------------


class TestLoadDiskImage:
    def test_loads_working_region_into_sectors(self, tmp_path):
        img = bf.DiskImage.blank()
        img.write_pattern(901, [[1, 0, 1, 0, 1, 0, 1, 0]] * 4)
        raw = img.to_disk_image_bytes()

        emu = se.PDDEmulator(tmp_path / "img")
        emu.load_disk_image(raw)

        assert emu._disk.read_sector(0) == raw[: se.SECTOR_SIZE]
        assert emu._disk.read_sector(1) == raw[se.SECTOR_SIZE : 2 * se.SECTOR_SIZE]

    def test_too_short_raises(self, tmp_path):
        emu = se.PDDEmulator(tmp_path / "img")
        with pytest.raises(ValueError):
            emu.load_disk_image(bytes(100))

    def test_pattern_survives_round_trip_through_disk(self, tmp_path):
        """
        Full pipeline: DiskImage → load_disk_image → read sectors back
        → DiskImage.from_bytes → read_pattern.
        """
        original_pat = [[1, 0, 1, 0], [0, 1, 0, 1], [1, 1, 0, 0]]
        img = bf.DiskImage.blank()
        img.write_pattern(901, original_pat)
        raw = img.to_disk_image_bytes()

        emu = se.PDDEmulator(tmp_path / "disk")
        emu.load_disk_image(raw)

        # Reconstruct working region from the emulator's sector files
        s0 = emu._disk.read_sector(0)
        s1 = emu._disk.read_sector(1)
        recovered_img = bf.DiskImage.from_bytes(s0 + s1)
        assert recovered_img.read_pattern(901) == original_pat
