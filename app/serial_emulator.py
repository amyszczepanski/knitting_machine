"""
serial_emulator.py — Tandy PDD1 floppy-drive emulator for the Brother KH-930/940.

The Brother KH-930/940 saves and loads patterns via a Tandy PDD1 external floppy
drive connected over a serial cable.  This module emulates that drive, letting
a computer stand in for the hardware.

PROTOCOL OVERVIEW
-----------------
The drive operates in two modes:

  OpMode  — the initial mode.  The machine sends "ZZ" followed by a framed
             request.  The only request the machine uses is 0x08, which
             switches the drive into FDC-emulation mode.

  FDC mode — the working mode.  The machine sends single-character ASCII
              commands with comma-separated parameters terminated by CR.
              Raw binary sector data flows in both directions.

OpMode request frame:
  ZZ  <req:1>  <len:1>  <data:len>  <checksum:1>
  checksum = ((req + len + sum(data)) % 256) ^ 0xFF

FDC command summary (only commands the Brother machine actually uses):
  F / G   Format disk — zeroes all 80 sectors; returns "00000000"; back to OpMode
  A       Read sector ID — returns 12-byte ID block for a sector
  R       Read logical sector — returns 1024 bytes of sector data
  S       Search for sector by ID — returns 4-byte match/no-match status
  B / C   Write sector ID — receives 12 bytes; updates stored ID
  W / X   Write logical sector — receives 1024 bytes; stores sector data

FDC status response: 8 uppercase hex-ASCII characters representing 4 bytes:
  bytes 1–2   error status  ("00" = ok, "80" = error, "40" = not found)
  bytes 3–4   physical sector number (PSN) in hex
  bytes 5–8   "0000"

IMPORTANT: the machine ignores lowercase hex digits; all status bytes must be
uppercase.  Raw sector data (R/A read responses and W/X/B/C received data) is
plain binary — NOT hex-encoded.

DISK LAYOUT
-----------
80 sectors × 1024 bytes each.  Sectors are persisted on disk as individual
files in a directory: NN.dat (1024 bytes) and NN.id (12 bytes) for sector N.
When an odd-numbered sector is written, sectors N-1 and N are concatenated
into file-MM.dat (where MM = (N-1)//2 + 1), matching the legacy behaviour.

THREAD SAFETY
-------------
PDDEmulator.run() is a blocking loop intended to run in a dedicated thread or
process.  Call stop() from another thread to request a clean shutdown.

INTEGRATION
-----------
Subclass or instantiate with a callback to receive notifications when the
machine completes a write cycle:

    def on_sector_pair_written(path: Path) -> None:
        img = DiskImage.from_bytes(path.read_bytes())
        ...

    emu = PDDEmulator(disk_dir, on_write=on_sector_pair_written)
    emu.run("/dev/tty.usbserial-FT3Q58M1")
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import serial  # pyserial

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SECTOR_SIZE: int = 1024
ID_SIZE: int = 12
NUM_SECTORS: int = 80

# FDC status responses (8 uppercase hex-ASCII chars = 4 bytes).
_STATUS_OK = b"00000000"
_STATUS_ERR = b"80000000"
_STATUS_NOT_FOUND = b"40000000"


def _status_ok(psn: int) -> bytes:
    """Build an 8-char OK status response with the sector number embedded."""
    return f"00{psn:02X}0000".encode("ascii")


# ---------------------------------------------------------------------------
# Sector storage
# ---------------------------------------------------------------------------


@dataclass
class _Sector:
    """
    One 1024-byte sector plus its 12-byte ID block, backed by two files.

    Files are opened lazily on first read/write to keep __init__ fast when
    building all 80 sectors.
    """

    dat_path: Path
    id_path: Path
    data: bytearray = field(default_factory=lambda: bytearray(SECTOR_SIZE))
    id_data: bytearray = field(default_factory=lambda: bytearray(ID_SIZE))

    @classmethod
    def open(cls, base: Path) -> "_Sector":
        """
        Load or initialise a sector from disk.

        `base` is the stem path (e.g. /img/03); the files /img/03.dat and
        /img/03.id are used.
        """
        dat_path = base.with_suffix(".dat")
        id_path = base.with_suffix(".id")

        # Data file
        if dat_path.exists():
            raw = dat_path.read_bytes()
            if len(raw) != SECTOR_SIZE:
                raise ValueError(
                    f"Sector file {dat_path} is {len(raw)} bytes; "
                    f"expected {SECTOR_SIZE}"
                )
            data = bytearray(raw)
        else:
            data = bytearray(SECTOR_SIZE)
            dat_path.write_bytes(bytes(data))

        # ID file
        if id_path.exists():
            raw = id_path.read_bytes()
            if len(raw) != ID_SIZE:
                raise ValueError(
                    f"ID file {id_path} is {len(raw)} bytes; " f"expected {ID_SIZE}"
                )
            id_data = bytearray(raw)
        else:
            id_data = bytearray(ID_SIZE)
            id_path.write_bytes(bytes(id_data))

        return cls(dat_path=dat_path, id_path=id_path, data=data, id_data=id_data)

    def read(self) -> bytes:
        return bytes(self.data)

    def write(self, data: bytes) -> None:
        if len(data) != SECTOR_SIZE:
            raise ValueError(
                f"Cannot write {len(data)} bytes to sector; " f"expected {SECTOR_SIZE}"
            )
        self.data = bytearray(data)
        self.dat_path.write_bytes(data)

    def read_id(self) -> bytes:
        return bytes(self.id_data)

    def write_id(self, id_data: bytes) -> None:
        if len(id_data) != ID_SIZE:
            raise ValueError(
                f"Cannot write {len(id_data)} bytes as ID; " f"expected {ID_SIZE}"
            )
        self.id_data = bytearray(id_data)
        self.id_path.write_bytes(id_data)

    def format(self) -> None:
        self.write(bytes(SECTOR_SIZE))
        self.write_id(bytes(ID_SIZE))


# ---------------------------------------------------------------------------
# Virtual disk
# ---------------------------------------------------------------------------


class _VirtualDisk:
    """
    80-sector virtual disk backed by files in a directory.

    Mirrors the legacy Disk + DiskSector classes but uses pathlib and
    bytearray throughout.
    """

    def __init__(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        self._dir = directory
        self._sectors: list[_Sector] = [
            _Sector.open(directory / f"{n:02d}") for n in range(NUM_SECTORS)
        ]
        self.last_written_pair: Path | None = None

    def _check_psn(self, psn: int) -> None:
        if not (0 <= psn < NUM_SECTORS):
            raise ValueError(f"PSN {psn} out of range 0–{NUM_SECTORS - 1}")

    def format(self) -> None:
        """Zero all sectors and IDs."""
        for sector in self._sectors:
            sector.format()
        self.last_written_pair = None
        logger.info("Disk formatted")

    def read_sector(self, psn: int) -> bytes:
        self._check_psn(psn)
        logger.debug("Read sector %d", psn)
        return self._sectors[psn].read()

    def write_sector(self, psn: int, data: bytes) -> None:
        """
        Write 1024 bytes to sector `psn`.

        When `psn` is odd, concatenate sector psn-1 and sector psn into
        file-MM.dat (MM = psn // 2 + 1), matching the legacy behaviour that
        the GUI used to detect a completed write cycle.
        """
        self._check_psn(psn)
        self._sectors[psn].write(data)
        logger.debug("Wrote sector %d", psn)

        if psn % 2 == 1:
            pair_num = psn // 2 + 1
            pair_path = self._dir / f"file-{pair_num:02d}.dat"
            combined = self._sectors[psn - 1].read() + self._sectors[psn].read()
            pair_path.write_bytes(combined)
            self.last_written_pair = pair_path
            logger.info("Sector pair written → %s", pair_path)

    def read_id(self, psn: int) -> bytes:
        self._check_psn(psn)
        return self._sectors[psn].read_id()

    def write_id(self, psn: int, id_data: bytes) -> None:
        self._check_psn(psn)
        self._sectors[psn].write_id(id_data)
        logger.debug("Wrote ID for sector %d", psn)

    def find_sector_by_id(self, start_psn: int, target_id: bytes) -> str:
        """
        Search sectors from `start_psn` onward for one whose ID matches
        `target_id`.  Returns an 8-char hex-ASCII status string:
          "00NNNN0000" on match (NN = sector number)
          "40000000"   if not found
        """
        for psn in range(start_psn, NUM_SECTORS):
            if self._sectors[psn].read_id() == target_id:
                return f"00{psn:02X}0000"
        return "40000000"


# ---------------------------------------------------------------------------
# Serial I/O helpers
# ---------------------------------------------------------------------------


class _SerialIO:
    """
    Thin wrapper around a pyserial Serial object providing the read/write
    primitives the emulator needs, all dealing in bytes/bytearray.
    """

    def __init__(self, port: serial.Serial) -> None:
        self._port = port

    def read_byte(self) -> int:
        """Block until one byte is available; return it as an int."""
        while True:
            b = self._port.read(1)
            if b:
                return b[0]

    def read_bytes(self, n: int) -> bytes:
        """Read exactly n bytes (blocking)."""
        buf = bytearray()
        while len(buf) < n:
            chunk = self._port.read(n - len(buf))
            if chunk:
                buf.extend(chunk)
        return bytes(buf)

    def read_until_cr(self) -> list[str]:
        """
        Read ASCII characters until CR, skip spaces, return comma-split tokens.
        This is how FDC command parameters arrive.
        """
        chars: list[str] = []
        while True:
            b = self.read_byte()
            ch = chr(b)
            if ch == "\r":
                break
            if ch != " ":
                chars.append(ch)
        return "".join(chars).split(",")

    def write(self, data: bytes) -> None:
        self._port.write(data)

    def write_status(self, status: bytes) -> None:
        """Write an 8-byte uppercase hex-ASCII status string."""
        assert len(status) == 8 and status == status.upper(), repr(status)
        self._port.write(status)

    def in_waiting(self) -> int:
        return self._port.in_waiting


# ---------------------------------------------------------------------------
# Protocol state machine
# ---------------------------------------------------------------------------


class PDDEmulator:
    """
    Tandy PDD1 floppy-drive emulator for the Brother KH-930/940 knitting machine.

    Parameters
    ----------
    disk_dir:
        Directory where sector files (NN.dat, NN.id) are read from and written
        to.  Created if it does not exist.  Pre-existing sector files are used
        as the initial disk state — load a DiskImage via brother_format and
        call write_sector_files() before starting the emulator to pre-populate
        the disk with a pattern.

    on_write:
        Optional callback invoked every time the machine completes writing an
        odd-sector (i.e. a full track pair).  Receives the Path of the
        file-MM.dat that was just written.  Called from the emulator thread.

    verbose:
        If True, log FDC commands at DEBUG level.
    """

    def __init__(
        self,
        disk_dir: str | Path,
        on_write: Callable[[Path], None] | None = None,
        verbose: bool = False,
    ) -> None:
        self._disk = _VirtualDisk(Path(disk_dir))
        self._on_write = on_write
        self._verbose = verbose
        self._fdc_mode = False
        self._stop_event = threading.Event()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def run(
        self,
        port: str = "/dev/tty.usbserial-FT3Q58M1",
        baudrate: int = 9600,
        idle_timeout: int = 300,
    ) -> None:
        """
        Open the serial port and run the emulator loop (blocking).

        The loop exits when any of the following occur:
        - stop() is called from another thread.
        - No byte arrives for `idle_timeout` consecutive seconds (i.e. the
          machine has gone quiet between top-level commands).  The counter
          resets to zero each time a byte is received, so a slow but active
          transfer will never trigger it.

        `idle_timeout` is intentionally not exposed in the API config; it is
        a temporary parameter that will become unnecessary once sector IDs are
        constructed locally rather than fetched from the machine.

        Call stop() from another thread to request a clean shutdown.
        """
        ser = serial.Serial(
            port=port,
            baudrate=baudrate,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            bytesize=serial.EIGHTBITS,
            timeout=1,  # read(1) returns b"" after 1 s of silence
            xonxoff=False,
            rtscts=False,
            dsrdtr=False,
        )
        ser.rts = True
        ser.dtr = True
        io = _SerialIO(ser)
        logger.info("PDDEmulator ready on %s at %d baud", port, baudrate)
        idle_seconds = 0
        try:
            while not self._stop_event.is_set():
                b = ser.read(1)
                if not b:
                    idle_seconds += 1
                    if idle_seconds >= idle_timeout:
                        logger.info(
                            "Idle timeout (%d s) — stopping emulator", idle_timeout
                        )
                        break
                    continue
                idle_seconds = 0
                logger.debug("RX byte: 0x%02X (%r)", b[0], chr(b[0]))
                self._dispatch(io, chr(b[0]))
        finally:
            ser.close()
            logger.info("PDDEmulator stopped")

    def stop(self) -> None:
        """Signal the run() loop to exit after the current operation."""
        self._stop_event.set()

    def populate_sector_files(
        self,
        dat_files: dict[int, bytes],
        id_files: dict[int, bytes],
    ) -> None:
        """
        Pre-populate the virtual disk from sector data and ID dicts before
        calling run().  Both dicts map sector number (0–79) to raw bytes.

        This is used to restore a disk state that was previously written by
        the machine (captured via read_sector_files()).  The machine writes
        its own sector IDs during a save operation; those IDs must be present
        for the machine to be able to locate the sectors on a subsequent load.

        Parameters
        ----------
        dat_files:
            Sector data bytes — each value must be exactly SECTOR_SIZE (1,024)
            bytes.  Sectors absent from the dict are left as blank zeros.
        id_files:
            Sector ID bytes — each value must be exactly ID_SIZE (12) bytes.
            Sectors absent from the dict are left as blank zeros.
        """
        for psn, data in dat_files.items():
            self._disk.write_sector(psn, data)
        for psn, id_data in id_files.items():
            self._disk.write_id(psn, id_data)
        logger.info(
            "Populated virtual disk: %d data sectors, %d ID sectors",
            len(dat_files),
            len(id_files),
        )

    def read_sector_files(self) -> tuple[dict[int, bytes], dict[int, bytes]]:
        """
        Export the current virtual disk state as two dicts mapping sector
        number to raw bytes.

        Returns
        -------
        dat_files:
            Sector data — sector number → 1,024 bytes.
        id_files:
            Sector IDs — sector number → 12 bytes.

        Call this after run() returns (i.e. after the machine has finished a
        save operation) to capture the sector files the machine wrote,
        including the IDs.  Persist the result and pass it back into
        populate_sector_files() before the next load operation.
        """
        dat_files: dict[int, bytes] = {}
        id_files: dict[int, bytes] = {}
        for psn in range(NUM_SECTORS):
            dat_files[psn] = self._disk.read_sector(psn)
            id_files[psn] = self._disk.read_id(psn)
        return dat_files, id_files

    # ------------------------------------------------------------------
    # Internal dispatch
    # ------------------------------------------------------------------

    def _dispatch(self, io: _SerialIO, ch: str) -> None:
        """Dispatch one already-read character through the protocol state machine."""
        if self._fdc_mode:
            self._handle_fdc(io, ch)
        else:
            if ch != "Z":
                return
            # Got first Z — peek at second byte
            second = io.read_byte()
            if chr(second) == "Z":
                self._handle_opmode(io)
            else:
                # Not a ZZ preamble — re-dispatch the second byte rather than
                # discarding it, to avoid a one-byte misalignment.
                self._dispatch(io, chr(second))

    # ------------------------------------------------------------------
    # OpMode
    # ------------------------------------------------------------------

    def _handle_opmode(self, io: _SerialIO) -> None:
        """
        Handle one OpMode request (already past the "ZZ" preamble).

        Frame: <req:1> <len:1> <data:len> <checksum:1>
        Only request 0x08 (switch to FDC mode) is used by the machine.
        """
        req = io.read_byte()
        length = io.read_byte()
        data = bytearray(io.read_bytes(length))
        checksum = io.read_byte()

        expected = ((req + length + sum(data)) % 256) ^ 0xFF
        if checksum != expected:
            logger.warning(
                "OpMode checksum mismatch: got 0x%02X expected 0x%02X",
                checksum,
                expected,
            )
            return

        if req == 0x08:
            logger.info("OpMode 0x08: switching to FDC mode")
            self._fdc_mode = True
        else:
            logger.warning("Unknown OpMode request 0x%02X — ignoring", req)

    # ------------------------------------------------------------------
    # FDC mode
    # ------------------------------------------------------------------

    def _handle_fdc(self, io: _SerialIO, cmd: str) -> None:
        """Dispatch one FDC command character."""
        if cmd == "\r":
            return

        if cmd == "Z":
            peek = io.read_byte()
            if chr(peek) == "Z":
                logger.info("Detected OpMode handshake in FDC mode — switching back")
                self._fdc_mode = False
                self._handle_opmode(io)
            elif peek:
                # Second byte wasn't Z, so this wasn't a ZZ preamble.
                # Dispatch the peeked byte as a normal FDC command rather
                # than discarding it, to avoid a one-byte misalignment.
                self._handle_fdc(io, chr(peek))
            return

        logger.debug("FDC command: %r (0x%02X)", cmd, ord(cmd))

        if cmd in ("F", "G"):
            self._cmd_format(io)
        elif cmd == "A":
            self._cmd_read_id(io)
        elif cmd == "R":
            self._cmd_read_sector(io)
        elif cmd == "S":
            self._cmd_search_id(io)
        elif cmd in ("B", "C"):
            self._cmd_write_id(io)
        elif cmd in ("W", "X"):
            self._cmd_write_sector(io)
        elif cmd == "M":
            logger.debug("FDC M (change modes) — not used by machine, ignoring")
        elif cmd == "D":
            logger.debug("FDC D (check device) — not used by machine, ignoring")
        else:
            logger.warning("Unknown FDC command %r — ignoring", cmd)

    # ------------------------------------------------------------------
    # FDC command implementations
    # ------------------------------------------------------------------

    def _read_psn(self, io: _SerialIO) -> int:
        """
        Read comma-separated params until CR, return PSN (first token as int,
        defaulting to 0).  The legacy code also accepted a second LSN token
        but always ignored it; we do the same.
        """
        tokens = io.read_until_cr()
        logger.debug("_read_psn tokens: %r", tokens)
        try:
            psn = int(tokens[0]) if tokens and tokens[0] else 0
        except ValueError:
            psn = 0
        return max(0, min(psn, NUM_SECTORS - 1))

    def _cmd_format(self, io: _SerialIO) -> None:
        """F/G — Format disk."""
        tokens = io.read_until_cr()
        format_sizes = {
            "0": 64,
            "1": 80,
            "2": 128,
            "3": 256,
            "4": 512,
            "5": 1024,
            "6": 1280,
        }
        code = tokens[0] if tokens else "5"
        bps = format_sizes.get(code, 1024)
        if bps != SECTOR_SIZE:
            logger.warning(
                "Format requested %d bytes/sector; machine uses %d — formatting anyway",
                bps,
                SECTOR_SIZE,
            )
        self._disk.format()
        io.write(_STATUS_OK)
        # After format, always return to OpMode
        self._fdc_mode = False
        logger.info("Disk formatted; returned to OpMode")

    def _cmd_read_id(self, io: _SerialIO) -> None:
        """A — Read sector ID (12 bytes)."""

        # FIXME introduce a delay in case this is what is causing my troubles
        # time.sleep(0.075)

        psn = self._read_psn(io)
        logger.debug("Read ID sector %d", psn)
        try:
            id_data = self._disk.read_id(psn)
        except Exception:
            logger.exception("Error reading ID for sector %d", psn)
            io.write(_STATUS_ERR)
            return

        io.write(_status_ok(psn))
        ack = io._port.read(1)
        logger.debug(
            "Read ID sector %d: ack byte = %r (0x%02X)",
            psn,
            chr(ack[0]) if ack else None,
            ack[0] if ack else 0,
        )
        if ack and chr(ack[0]) == "\r":
            logger.debug("Read ID sector %d: ID bytes = %s", psn, id_data.hex())
            io.write(id_data)
            logger.debug("Read ID sector %d: sent 12 bytes", psn)
        else:
            logger.warning(
                "Read ID sector %d: expected CR ack, got %r — not sending data",
                psn,
                ack,
            )

    def _cmd_read_sector(self, io: _SerialIO) -> None:
        """R — Read one logical sector (1024 bytes)."""
        psn = self._read_psn(io)
        logger.debug("Read sector %d", psn)
        try:
            data = self._disk.read_sector(psn)
        except Exception:
            logger.exception("Error reading sector %d", psn)
            io.write(_STATUS_ERR)
            return

        io.write(_status_ok(psn))
        ack = io._port.read(1)
        if ack and chr(ack[0]) == "\r":
            io.write(data)
            logger.debug("Read sector %d: sent 1024 bytes", psn)
        else:
            logger.warning(
                "Read sector %d: expected CR ack, got %r — not sending data", psn, ack
            )

    def _cmd_search_id(self, io: _SerialIO) -> None:
        """S — Search for a sector by its 12-byte ID."""
        psn = self._read_psn(io)
        # Send initial status (acknowledges receipt of the command + PSN),
        # then receive the 12-byte ID to search for, then send the result.
        io.write(_status_ok(psn))
        target_id = io.read_bytes(ID_SIZE)
        logger.debug("Search ID starting at sector %d, target=%s", psn, target_id.hex())
        result = self._disk.find_sector_by_id(psn, target_id)
        io.write(result.encode("ascii"))

    def _cmd_write_id(self, io: _SerialIO) -> None:
        """B/C — Write sector ID (12 bytes)."""
        psn = self._read_psn(io)
        io.write(_status_ok(psn))
        id_data = io.read_bytes(ID_SIZE)
        logger.debug("Write ID sector %d", psn)
        try:
            self._disk.write_id(psn, id_data)
        except Exception:
            logger.exception("Error writing ID for sector %d", psn)
            io.write(_STATUS_ERR)
            return
        io.write(_status_ok(psn))

    def _cmd_write_sector(self, io: _SerialIO) -> None:
        """W/X — Write one logical sector (1024 bytes)."""
        psn = self._read_psn(io)
        io.write(_status_ok(psn))
        data = io.read_bytes(SECTOR_SIZE)
        logger.debug("Write sector %d", psn)
        try:
            self._disk.write_sector(psn, data)
        except Exception:
            logger.exception("Error writing sector %d", psn)
            io.write(_STATUS_ERR)
            return

        io.write(_status_ok(psn))

        # Fire the write callback if a pair was completed
        if self._on_write and self._disk.last_written_pair is not None:
            try:
                self._on_write(self._disk.last_written_pair)
            except Exception:
                logger.exception("on_write callback raised")


# ---------------------------------------------------------------------------
# Convenience entry point (mirrors legacy command-line usage)
# ---------------------------------------------------------------------------


def main() -> None:
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Brother KH-930/940 floppy drive emulator (Tandy PDD1)"
    )
    parser.add_argument(
        "disk_dir", help="Directory for sector files (created if absent)"
    )
    parser.add_argument(
        "serial_port", help="Serial device, e.g. /dev/tty.usbserial-FT3Q58M1"
    )
    parser.add_argument(
        "--baud", type=int, default=9600, help="Baud rate (default: 9600)"
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Enable DEBUG logging"
    )
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    emu = PDDEmulator(args.disk_dir, verbose=args.verbose)
    logger.info("PDDEmulator ready — Ctrl-C to stop")
    try:
        emu.run(port=args.serial_port, baudrate=args.baud)
    except KeyboardInterrupt:
        emu.stop()
        logger.info("Stopped")


if __name__ == "__main__":
    main()
