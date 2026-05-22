"""
app/api.py — FastAPI application for the Brother KH-940 knitting machine.

Endpoints
---------
POST /pattern
    Upload a 1-bit (or any) image and write it as a pattern onto the
    current in-memory disk image.  Returns the pattern number and
    dimensions.

GET /patterns
    List all patterns currently stored in the in-memory disk image.

POST /send
    Send the current disk image to the machine via the serial emulator.
    The emulator runs in a background thread; this endpoint returns
    immediately with a task ID.  Poll GET /send/{task_id} for status.

GET /send/{task_id}
    Return the status of a send task ("pending" | "running" | "done" |
    "error").

GET /preview
    Accept an image upload (multipart) and return the scaled/cropped
    1-bit preview as a PNG, without touching the disk image.  Useful
    for the frontend live-preview feature.

All state (disk image, emulator thread) lives in app-level singletons so
that a single `uvicorn app.api:app` process holds the machine state.

Logging
-------
Hardware-interaction events are logged to logs/knitting_machine.log
(rotating, 5 MB × 3 backups) and mirrored to stderr.  The logger name
is "knitting_machine".  Set the LOG_LEVEL environment variable to
override the default level (INFO).
"""

from __future__ import annotations

import base64
import io
import logging
import logging.handlers
import os
import threading
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from PIL import Image
from pydantic import BaseModel

from app.brother_format import DiskImage, MachineModel
from app.image import DitherMode, ImageError, Rotation, load_image
from app.ports import PortDiscoveryError, PortInfo, discover_ftdi_port, list_all_ports

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

_LOG_DIR = Path("logs")
_LOG_DIR.mkdir(exist_ok=True)

_LOG_FILE = _LOG_DIR / "knitting_machine.log"
_LOG_FORMAT = "%(asctime)s  %(levelname)-8s  %(threadName)s  %(message)s"
_LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_log_level = getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO)

_file_handler = logging.handlers.RotatingFileHandler(
    _LOG_FILE,
    maxBytes=5 * 1024 * 1024,  # 5 MB
    backupCount=3,
    encoding="utf-8",
)
_file_handler.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_LOG_DATE_FORMAT))

_stderr_handler = logging.StreamHandler()
_stderr_handler.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_LOG_DATE_FORMAT))

log = logging.getLogger("knitting_machine")
log.setLevel(_log_level)
log.addHandler(_file_handler)
log.addHandler(_stderr_handler)
log.propagate = False  # don't double-log via the root logger

logging.getLogger("app.serial_emulator").setLevel(_log_level)

# ---------------------------------------------------------------------------
# Application state
# ---------------------------------------------------------------------------


class _AppState:
    """Single-process mutable state.  Not thread-safe for concurrent
    pattern writes; good enough for a single-user machine-side tool."""

    def __init__(self) -> None:
        self.model: MachineModel = MachineModel.KH940
        self.disk: DiskImage = DiskImage.blank(self.model)
        # My cable is /dev/tty.usbserial-FT3Q58M1
        self.serial_port: str = ""
        self.baud_rate: int = 9600
        self.disk_dir: str = "/tmp/knitting_disk"
        self.tasks: dict[str, "_TaskState"] = {}
        # Sector files persisted from the last machine save operation.
        # These are the .dat and .id files the machine itself wrote, including
        # the sector IDs the machine generated.  They must be fed back into the
        # emulator on the next load operation so the machine can find its own
        # sectors by ID.  Both dicts map sector number (0–79) → raw bytes.
        self.sector_dat: dict[int, bytes] = {}
        self.sector_id: dict[int, bytes] = {}


_state = _AppState()


def _startup_discover_port() -> None:
    """Attempt FTDI port discovery when the server starts.

    On success, _state.serial_port is set and a single INFO line is logged.
    On failure, _state.serial_port is left as an empty string and a WARNING
    is logged.  POST /send and POST /receive will refuse to run until the
    port is set via PUT /config.
    """
    if _state.serial_port:
        return
    try:
        port = discover_ftdi_port()
        _state.serial_port = port.device
        log.info(
            "Auto-discovered FTDI serial port: %s (%s)",
            port.device,
            port.description,
        )
    except PortDiscoveryError as exc:
        _state.serial_port = ""
        log.warning(
            "Port auto-discovery failed: %s  "
            "Use PUT /config to set the port manually.  "
            "Available ports: %s",
            exc,
            [p.device for p in exc.all_ports] or "none",
        )


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    _startup_discover_port()
    yield


# ---------------------------------------------------------------------------
# Application & CORS
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Knitting Machine API",
    description="Brother KH-940 pattern management and upload.",
    version="0.1.0",
    lifespan=_lifespan,
)

app.mount("/static", StaticFiles(directory="app/static"), name="static")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten in production
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Moar state
# ---------------------------------------------------------------------------


class _TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"
    TIMED_OUT = "timed_out"


@dataclass
class _TaskState:
    status: _TaskStatus = _TaskStatus.PENDING
    error: str | None = None


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class PatternInfo(BaseModel):
    number: int
    rows: int
    stitches: int


class DiskStatusResponse(BaseModel):
    patterns: list[PatternInfo]
    bytes_remaining: int
    bytes_total: int
    slots_used: int
    slots_total: int


class PatternListResponse(BaseModel):
    patterns: list[PatternInfo]


class WritePatternResponse(BaseModel):
    number: int
    width: int
    height: int
    orig_width: int
    orig_height: int


class SendResponse(BaseModel):
    task_id: str
    status: _TaskStatus


class TaskStatusResponse(BaseModel):
    task_id: str
    status: _TaskStatus
    error: str | None = None


class PreviewResponse(BaseModel):
    """PNG image encoded as a base64 data-URI string."""

    width: int
    height: int
    data_uri: str


class PatternPixelsResponse(BaseModel):
    """Pixel grid and memo values for a committed pattern."""

    number: int
    pixels: list[list[int]]
    memo: list[int]
    width: int
    height: int


class PatternEditRequest(BaseModel):
    """Edited pixel grid and memo values to write back for a pattern."""

    pixels: list[list[int]]
    memo: list[int]


class ConfigRequest(BaseModel):
    serial_port: str | None = None
    baud_rate: int | None = None
    disk_dir: str | None = None


class ConfigResponse(BaseModel):
    serial_port: str
    baud_rate: int
    disk_dir: str


class PortInfoResponse(BaseModel):
    device: str
    description: str
    manufacturer: str | None
    vid: str | None  # rendered as hex string, e.g. "0x0403"
    pid: str | None
    serial_number: str | None
    is_ftdi: bool


class PortListResponse(BaseModel):
    ports: list[PortInfoResponse]
    ftdi_candidates: list[str]  # device names only, for quick scanning


def _port_info_to_response(p: PortInfo) -> PortInfoResponse:
    return PortInfoResponse(
        device=p.device,
        description=p.description,
        manufacturer=p.manufacturer,
        vid=hex(p.vid) if p.vid is not None else None,
        pid=hex(p.pid) if p.pid is not None else None,
        serial_number=p.serial_number,
        is_ftdi=p.is_ftdi,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _bytes_from_upload(upload: UploadFile) -> bytes:
    upload.file.seek(0)
    data = upload.file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    return data


def _render_preview_png(pixel_rows: list[list[int]]) -> bytes:
    """Convert pixel rows (0/1) to a black-and-white PNG as bytes."""
    h = len(pixel_rows)
    w = len(pixel_rows[0]) if h else 0
    img = Image.new("L", (w, h), 255)
    pixels = img.load()
    for y, row in enumerate(pixel_rows):
        for x, val in enumerate(row):
            pixels[x, y] = 0 if val == 1 else 255  # type: ignore[index]
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _run_receive(task_id: str) -> None:
    """Background thread: run the emulator in receive mode.

    The machine initiates a save operation.  The emulator accepts whatever
    the machine writes — sector data and sector IDs — and on completion
    persists the full sector state into _state.sector_dat / _state.sector_id.
    The in-memory DiskImage is then rebuilt from the received data so that
    GET /patterns reflects what was saved.
    """
    task = _state.tasks[task_id]
    task.status = _TaskStatus.RUNNING

    port = _state.serial_port
    baud = _state.baud_rate

    log.info(
        "Receive task %s started — port=%s  baud=%d",
        task_id,
        port,
        baud,
    )

    try:
        log.info("[%s] Importing serial emulator", task_id)
        from app.serial_emulator import PDDEmulator  # noqa: PLC0415
        import tempfile  # noqa: PLC0415

        log.info(
            "[%s] Creating PDDEmulator (blank disk — waiting for machine to write)",
            task_id,
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            emulator = PDDEmulator(disk_dir=tmpdir)

            log.info(
                "[%s] Opening serial port %s at %d baud — waiting for machine",
                task_id,
                port,
                baud,
            )
            emulator.run(port=port, baudrate=baud, idle_timeout=10)
            log.info("[%s] emulator.run() returned — save complete", task_id)

            # ---- Capture everything the machine wrote ----------------------
            dat_files, id_files = emulator.read_sector_files()

        # Persist sector state so the next send can serve it back.
        _state.sector_dat = dat_files
        _state.sector_id = id_files
        log.info(
            "[%s] Persisted %d sector data + %d sector ID files",
            task_id,
            len(dat_files),
            len(id_files),
        )

        # Rebuild the in-memory DiskImage from received data.
        working_sectors = 32  # KH-940 uses first 32 sectors
        working_bytes = b"".join(
            dat_files.get(n, bytes(1024)) for n in range(working_sectors)
        )
        try:
            _state.disk = DiskImage.from_bytes(working_bytes, _state.model)
            log.info(
                "[%s] Rebuilt DiskImage — %d pattern(s) found",
                task_id,
                len(_state.disk.list_patterns()),
            )
        except Exception as exc:
            log.warning(
                "[%s] Could not rebuild DiskImage from received data: %s",
                task_id,
                exc,
            )

        task.status = _TaskStatus.DONE
        log.info("[%s] Receive task finished successfully", task_id)

    except Exception as exc:
        task.status = _TaskStatus.ERROR
        task.error = str(exc)
        log.error("[%s] Receive task failed: %s", task_id, exc, exc_info=True)


def _run_send(task_id: str) -> None:
    """Background thread: serve the current disk image to the machine.

    The machine initiates a load operation.  The emulator populates sector
    data from the in-memory DiskImage and generates sector IDs synthetically
    via DiskImage.to_id_files() — no prior receive operation is required.
    """
    task = _state.tasks[task_id]
    task.status = _TaskStatus.RUNNING

    port = _state.serial_port
    baud = _state.baud_rate

    patterns = _state.disk.list_patterns()
    log.info(
        "Send task %s started — port=%s  baud=%d  patterns=%d",
        task_id,
        port,
        baud,
        len(patterns),
    )

    try:
        log.info("[%s] Importing serial emulator", task_id)
        from app.serial_emulator import PDDEmulator  # noqa: PLC0415
        import tempfile  # noqa: PLC0415

        dat_files = _state.disk.to_sector_files()
        id_files = _state.disk.to_id_files()

        log.info("[%s] Creating PDDEmulator and populating sector files", task_id)
        with tempfile.TemporaryDirectory() as tmpdir:
            emulator = PDDEmulator(disk_dir=tmpdir)
            emulator.populate_sector_files(dat_files, id_files)
            log.info(
                "[%s] Sector files populated (%d data, %d ID)",
                task_id,
                len(dat_files),
                len(id_files),
            )

            log.info(
                "[%s] Opening serial port %s at %d baud — waiting for machine",
                task_id,
                port,
                baud,
            )
            # This hardcodes in sector 31, which is the end of the data for the KH-940
            emulator.run(port=port, baudrate=baud, stop_after_sector=31)

            expected = 32
            if emulator._sectors_sent >= expected:
                task.status = _TaskStatus.DONE
                log.info(
                    "[%s] Send task finished successfully — all %d sectors sent",
                    task_id,
                    expected,
                )
            else:
                task.status = _TaskStatus.TIMED_OUT
                log.warning(
                    "[%s] Send task timed out — only %d of %d sectors sent",
                    task_id,
                    emulator._sectors_sent,
                    expected,
                )

    except Exception as exc:
        task.status = _TaskStatus.ERROR
        task.error = str(exc)
        log.error("[%s] Send task failed: %s", task_id, exc, exc_info=True)


def _require_serial_port() -> str:
    """Return the configured serial port, or raise HTTP 503.

    The error body includes available ports so the client can present them
    to the user without needing a separate GET /ports call.
    """
    if not _state.serial_port:
        available = [_port_info_to_response(p) for p in list_all_ports()]
        raise HTTPException(
            status_code=503,
            detail={
                "message": (
                    "No serial port configured. "
                    "Use PUT /config to set one, or GET /ports for available options."
                ),
                "available_ports": [p.model_dump() for p in available],
            },
        )
    return _state.serial_port


# ---------------------------------------------------------------------------
# Image parameter helpers
# ---------------------------------------------------------------------------


def _parse_crop(
    left: int, upper: int, right: int, lower: int
) -> tuple[int, int, int, int] | None:
    """Convert four crop edge values from form fields into a crop tuple.

    The frontend sends 0 for crop_right / crop_lower to mean "no crop"
    (since the original image size isn't known at form-submit time).
    Returns None when no meaningful crop region is specified.
    """
    if right > left and lower > upper:
        return (left, upper, right, lower)
    return None


def _validated_rotation(value: int) -> Rotation:
    if value not in (0, 90, 180, 270):
        raise HTTPException(
            status_code=422,
            detail=f"rotation must be 0, 90, 180, or 270; got {value}",
        )
    return value  # type: ignore[return-value]


def _validated_dither(value: str) -> DitherMode:
    allowed = ("none", "floyd-steinberg", "bayer")
    if value not in allowed:
        raise HTTPException(
            status_code=422,
            detail=f"dither must be one of {allowed}; got {value!r}",
        )
    return value  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/patterns", response_model=PatternListResponse)
def list_patterns() -> PatternListResponse:
    """Return all patterns currently in the in-memory disk image."""
    patterns: list[PatternInfo] = []
    for number in range(901, 1000):
        try:
            pixel_rows = _state.disk.read_pattern(number)
        except Exception:
            continue
        if pixel_rows:
            patterns.append(
                PatternInfo(
                    number=number,
                    rows=len(pixel_rows),
                    stitches=len(pixel_rows[0]),
                )
            )
    return PatternListResponse(patterns=patterns)


@app.get("/disk/status", response_model=DiskStatusResponse)
def disk_status() -> DiskStatusResponse:
    """Return capacity and pattern list for the in-memory disk image.

    Combines the pattern list with storage metrics so the frontend can
    display a capacity indicator without a separate round-trip.
    """
    entries = _state.disk.list_patterns()
    patterns = [
        PatternInfo(number=e.number, rows=e.rows, stitches=e.stitches) for e in entries
    ]
    return DiskStatusResponse(
        patterns=patterns,
        bytes_remaining=_state.disk.bytes_remaining,
        bytes_total=_state.disk._init_pattern_offset,
        slots_used=_state.disk._next_slot,
        slots_total=_state.disk._max_patterns,
    )


@app.get("/disk/download")
def download_disk() -> Response:
    """Download the current in-memory disk image as a raw 81,920-byte binary blob.

    The file can be re-uploaded later via POST /disk/upload to restore the
    full pattern set.  It is also a valid input for any tool that reads
    Brother KH-940 disk images.
    """
    blob = _state.disk.to_disk_image_bytes()
    log.info("Disk image downloaded (%d bytes)", len(blob))
    return Response(
        content=blob,
        media_type="application/octet-stream",
        headers={"Content-Disposition": 'attachment; filename="knitting_disk.dat"'},
    )


@app.post("/disk/upload")
async def upload_disk(
    file: Annotated[UploadFile, File(description="Raw 81,920-byte Brother disk image")],
    force: Annotated[
        bool,
        Form(
            description=(
                "If true, replace the current disk image even if it contains patterns. "
                "If false (default) and the RAM disk is non-empty, return 409."
            )
        ),
    ] = False,
) -> dict[str, object]:
    """Replace the in-memory disk image from an uploaded binary blob.

    Accepts either the full 81,920-byte disk image or just the 32,768-byte
    KH-940 working region.

    If the current RAM disk already contains patterns and ``force`` is not
    set, the request is rejected with HTTP 409 so the frontend can prompt
    the user for confirmation before overwriting unsaved work.
    """
    raw = _bytes_from_upload(file)

    # Guard: warn before overwriting a non-empty disk.
    existing = _state.disk.list_patterns()
    if existing and not force:
        raise HTTPException(
            status_code=409,
            detail={
                "message": (
                    "The current disk image contains "
                    f"{len(existing)} pattern(s). "
                    "Upload with force=true to overwrite, or reset the disk first."
                ),
                "pattern_count": len(existing),
            },
        )

    try:
        new_disk = DiskImage.from_bytes(raw, _state.model)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Could not parse disk image: {exc}",
        )

    _state.disk = new_disk
    patterns = _state.disk.list_patterns()
    log.info(
        "Disk image uploaded — %d pattern(s) restored, %d bytes remaining",
        len(patterns),
        _state.disk.bytes_remaining,
    )
    return {
        "status": "ok",
        "patterns_restored": len(patterns),
        "bytes_remaining": _state.disk.bytes_remaining,
    }


@app.get("/preview/pattern/{number}", response_model=PreviewResponse)
def preview_pattern(number: int) -> PreviewResponse:
    """Return a PNG preview for a pattern already stored in the RAM disk.

    This uses the same rendering path as POST /preview but reads pixel
    data from the disk image rather than an uploaded image file.  Intended
    for the pattern list thumbnails in the frontend.
    """
    try:
        pixel_rows = _state.disk.read_pattern(number)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=f"Pattern {number} not found in disk image.",
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to read pattern {number}: {exc}",
        )

    png_bytes = _render_preview_png(pixel_rows)
    data_uri = "data:image/png;base64," + base64.b64encode(png_bytes).decode()
    h = len(pixel_rows)
    w = len(pixel_rows[0]) if h else 0
    return PreviewResponse(width=w, height=h, data_uri=data_uri)


@app.delete("/pattern/{number}")
def delete_pattern(number: int) -> dict[str, object]:
    """Delete a single pattern from the in-memory disk image.

    The pattern data is removed and the directory is compacted in-place by
    rebuilding the disk image from the remaining patterns.  All pattern
    numbers and their data are preserved; only the deleted pattern is lost.

    Raises 404 if the pattern does not exist.
    """
    entry = _state.disk.get_pattern_entry(number)
    if entry is None:
        raise HTTPException(
            status_code=404,
            detail=f"Pattern {number} not found in disk image.",
        )

    # Read all surviving patterns before we touch anything.
    survivors: list[tuple[int, list[list[int]], list[int]]] = []
    for e in _state.disk.list_patterns():
        if e.number == number:
            continue
        try:
            pixel_rows = _state.disk.read_pattern(e.number)
            memo = _state.disk.read_memo(e.number)
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to read pattern {e.number} during compaction: {exc}",
            )
        survivors.append((e.number, pixel_rows, memo))

    # Rebuild from scratch with the survivors.
    new_disk = DiskImage.blank(_state.model)
    for pat_number, pixel_rows, memo in survivors:
        try:
            new_disk.write_pattern(pat_number, pixel_rows, memo)
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to re-write pattern {pat_number} during compaction: {exc}",
            )

    _state.disk = new_disk
    log.info(
        "Pattern %d deleted — %d pattern(s) remaining, %d bytes remaining",
        number,
        len(survivors),
        _state.disk.bytes_remaining,
    )
    return {
        "status": "ok",
        "deleted": number,
        "patterns_remaining": len(survivors),
        "bytes_remaining": _state.disk.bytes_remaining,
    }


@app.get("/pattern/{number}/pixels", response_model=PatternPixelsResponse)
def get_pattern_pixels(number: int) -> PatternPixelsResponse:
    """Return the pixel grid and memo values for a committed pattern.

    Used by the Stage 2 pixel editor to load a pattern for editing.
    Raises 404 if the pattern does not exist.
    """
    entry = _state.disk.get_pattern_entry(number)
    if entry is None:
        raise HTTPException(
            status_code=404,
            detail=f"Pattern {number} not found in disk image.",
        )

    try:
        pixels = _state.disk.read_pattern(number)
        memo = _state.disk.read_memo(number)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to read pattern {number}: {exc}",
        )

    h = len(pixels)
    w = len(pixels[0]) if h else 0
    return PatternPixelsResponse(
        number=number,
        pixels=pixels,
        memo=memo,
        width=w,
        height=h,
    )


@app.put("/pattern/{number}", response_model=WritePatternResponse)
def edit_pattern(number: int, req: PatternEditRequest) -> WritePatternResponse:
    """Overwrite an existing committed pattern with edited pixel and memo data.

    Performs a delete-then-rewrite compaction internally so the caller does not
    need to orchestrate two separate requests.  All other patterns are preserved.

    Raises 404 if the pattern does not exist.
    Raises 422 if the pixel data is invalid (empty, unequal row widths, stitch
    count out of range) or if any memo value is outside 0–15.
    """

    entry = _state.disk.get_pattern_entry(number)
    if entry is None:
        raise HTTPException(
            status_code=404,
            detail=f"Pattern {number} not found in disk image.",
        )

    # --- Validate pixels ---
    pixels = req.pixels
    if not pixels:
        raise HTTPException(status_code=422, detail="pixels must not be empty.")
    stitches = len(pixels[0])
    if stitches == 0 or stitches > 200:
        raise HTTPException(
            status_code=422,
            detail=f"Stitch count {stitches} is out of range 1–200.",
        )
    for i, row in enumerate(pixels):
        if len(row) != stitches:
            raise HTTPException(
                status_code=422,
                detail=f"Row {i} has {len(row)} stitches; expected {stitches}.",
            )
        for j, val in enumerate(row):
            if val not in (0, 1):
                raise HTTPException(
                    status_code=422,
                    detail=f"pixels[{i}][{j}] = {val!r}; must be 0 or 1.",
                )

    # --- Validate memo ---
    memo = req.memo
    for i, val in enumerate(memo):
        if not (0 <= val <= 15):
            raise HTTPException(
                status_code=422,
                detail=f"memo[{i}] = {val!r}; must be 0–15.",
            )

    # --- Read all surviving patterns (everyone except the one being edited) ---
    all_patterns: list[tuple[int, list[list[int]], list[int]]] = []
    for e in _state.disk.list_patterns():
        if e.number == number:
            continue
        try:
            survivor_pixels = _state.disk.read_pattern(e.number)
            survivor_memo = _state.disk.read_memo(e.number)
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to read pattern {e.number} during compaction: {exc}",
            )
        all_patterns.append((e.number, survivor_pixels, survivor_memo))

    # Include the edited pattern and sort by number so that write order matches
    # slot order.  list_patterns() stops at the first FINHDR/empty slot, so
    # every valid pattern must occupy a contiguous run of slots starting at 0;
    # writing out of numerical order would place the FINHDR before some entries
    # and cause them to be invisible to subsequent reads.
    all_patterns.append((number, pixels, memo))
    all_patterns.sort(key=lambda t: t[0])

    # --- Rebuild disk from scratch with all patterns in slot order ---
    new_disk = DiskImage.blank(_state.model)
    for pat_number, pat_pixels, pat_memo in all_patterns:
        try:
            new_disk.write_pattern(pat_number, pat_pixels, pat_memo)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to re-write pattern {pat_number} during compaction: {exc}",
            )

    _state.disk = new_disk
    rows = len(pixels)
    log.info(
        "Pattern %d edited — %d stitches × %d rows, %d bytes remaining",
        number,
        stitches,
        rows,
        _state.disk.bytes_remaining,
    )
    return WritePatternResponse(
        number=number,
        width=stitches,
        height=rows,
        orig_width=stitches,
        orig_height=rows,
    )


@app.post("/pattern", response_model=WritePatternResponse)
def write_pattern(
    file: Annotated[UploadFile, File(description="1-bit image to knit")],
    number: Annotated[
        int,
        Form(description="Pattern number 901–999", ge=901, le=999),
    ] = 901,
    threshold: Annotated[
        int,
        Form(description="Binarisation threshold 0–255", ge=0, le=255),
    ] = 128,
    stitch_aspect_ratio: Annotated[
        float,
        Form(
            description=(
                "Vertical stretch factor to compensate for non-square stitch "
                "aspect ratio. 1.33 (4:3) is a good default for most yarns."
            ),
            gt=0,
        ),
    ] = 4
    / 3,
    flip_horizontal: Annotated[
        bool,
        Form(description="Mirror the image left-to-right before scaling."),
    ] = False,
    rotation: Annotated[
        int,
        Form(description="Clockwise rotation in degrees: 0, 90, 180, or 270."),
    ] = 0,
    invert: Annotated[
        bool,
        Form(description="Swap knit (1) and background (0) after binarisation."),
    ] = False,
    dither: Annotated[
        str,
        Form(description="Binarisation method: 'none', 'floyd-steinberg', or 'bayer'."),
    ] = "none",
    crop_left: Annotated[
        int, Form(description="Crop left edge (original pixels).")
    ] = 0,
    crop_upper: Annotated[
        int, Form(description="Crop upper edge (original pixels).")
    ] = 0,
    crop_right: Annotated[int, Form(description="Crop right edge (0 = no crop).")] = 0,
    crop_lower: Annotated[int, Form(description="Crop lower edge (0 = no crop).")] = 0,
) -> WritePatternResponse:
    """Upload an image and write it as a knitting pattern.

    The image is scaled to ≤ 200 stitches wide, stretched vertically by
    stitch_aspect_ratio to correct for non-square stitch proportions,
    binarised, and encoded into the Brother disk image format.  The pattern
    can then be sent to the machine via POST /send.
    """
    raw = _bytes_from_upload(file)
    crop = _parse_crop(crop_left, crop_upper, crop_right, crop_lower)
    try:
        result = load_image(
            raw,
            threshold=threshold,
            stitch_aspect_ratio=stitch_aspect_ratio,
            max_rows=_state.disk.max_rows,
            flip_horizontal=flip_horizontal,
            rotation=_validated_rotation(rotation),
            invert=invert,
            dither=_validated_dither(dither),
            crop=crop,
        )
    except ImageError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    try:
        _state.disk.write_pattern(number, result.rows)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to encode pattern into disk image: {exc}",
        )

    return WritePatternResponse(
        number=number,
        width=result.width,
        height=result.height,
        orig_width=result.orig_width,
        orig_height=result.orig_height,
    )


@app.post("/preview", response_model=PreviewResponse)
def preview_image(
    file: Annotated[UploadFile, File(description="Image to preview")],
    threshold: Annotated[int, Form(ge=0, le=255)] = 128,
    stitch_aspect_ratio: Annotated[float, Form(gt=0)] = 4 / 3,
    flip_horizontal: Annotated[bool, Form()] = False,
    rotation: Annotated[int, Form()] = 0,
    invert: Annotated[bool, Form()] = False,
    dither: Annotated[str, Form()] = "none",
    crop_left: Annotated[int, Form()] = 0,
    crop_upper: Annotated[int, Form()] = 0,
    crop_right: Annotated[int, Form()] = 0,
    crop_lower: Annotated[int, Form()] = 0,
) -> PreviewResponse:
    """Return a scaled/binarised preview PNG without writing to disk."""
    raw = _bytes_from_upload(file)
    crop = _parse_crop(crop_left, crop_upper, crop_right, crop_lower)
    try:
        result = load_image(
            raw,
            threshold=threshold,
            stitch_aspect_ratio=stitch_aspect_ratio,
            max_rows=_state.disk.max_rows,
            flip_horizontal=flip_horizontal,
            rotation=_validated_rotation(rotation),
            invert=invert,
            dither=_validated_dither(dither),
            crop=crop,
        )
    except ImageError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    png_bytes = _render_preview_png(result.rows)
    data_uri = "data:image/png;base64," + base64.b64encode(png_bytes).decode()
    return PreviewResponse(
        width=result.width,
        height=result.height,
        data_uri=data_uri,
    )


@app.post("/send", response_model=SendResponse)
def send_to_machine() -> SendResponse:
    """Serve the persisted sector files to the machine for a load operation.

    Returns a task_id; poll GET /send/{task_id} for status.
    """
    # Prevent two emulator tasks from opening the serial port simultaneously.
    for task in _state.tasks.values():
        if task.status == _TaskStatus.RUNNING:
            raise HTTPException(
                status_code=409,
                detail="An emulator task is already running. Wait for it to finish or stop it first.",
            )

    _require_serial_port()  # raises 503 if no port is configured

    task_id = str(uuid.uuid4())
    _state.tasks[task_id] = _TaskState()

    log.info(
        "Queuing send task %s — %d pattern(s) in disk image, port=%s",
        task_id,
        len(_state.disk.list_patterns()),
        _state.serial_port,
    )

    thread = threading.Thread(
        target=_run_send,
        args=(task_id,),
        daemon=True,
        name=f"pdd-emulator-{task_id[:8]}",
    )
    thread.start()

    return SendResponse(task_id=task_id, status=_TaskStatus.PENDING)


@app.post("/receive", response_model=SendResponse)
def receive_from_machine() -> SendResponse:
    """Run the emulator in receive mode so the machine can save to it.

    Start this endpoint, then initiate a save on the KH-940 keypad.  The
    emulator accepts the machine's write operations — sector data and sector
    IDs — and on completion:

      1. Persists the sector files (including machine-written IDs) so that
         they can be examined or reused (if necessary).
      2. Rebuilds the in-memory DiskImage from the received data so that
         GET /patterns reflects what was just saved.

    Returns a task_id; poll GET /send/{task_id} for status.
    """
    # Prevent two emulator tasks from opening the serial port simultaneously.
    for task in _state.tasks.values():
        if task.status == _TaskStatus.RUNNING:
            raise HTTPException(
                status_code=409,
                detail="An emulator task is already running. Wait for it to finish or stop it first.",
            )

    _require_serial_port()  # raises 503 if no port is configured

    task_id = str(uuid.uuid4())
    _state.tasks[task_id] = _TaskState()

    log.info(
        "Queuing receive task %s — port=%s",
        task_id,
        _state.serial_port,
    )

    thread = threading.Thread(
        target=_run_receive,
        args=(task_id,),
        daemon=True,
        name=f"pdd-emulator-{task_id[:8]}",
    )
    thread.start()

    return SendResponse(task_id=task_id, status=_TaskStatus.PENDING)


@app.get("/send/{task_id}", response_model=TaskStatusResponse)
def send_status(task_id: str) -> TaskStatusResponse:
    """Poll the status of a send task."""
    task = _state.tasks.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found.")
    return TaskStatusResponse(
        task_id=task_id,
        status=task.status,
        error=task.error,
    )


@app.get("/config", response_model=ConfigResponse)
def get_config() -> ConfigResponse:
    return ConfigResponse(
        serial_port=_state.serial_port,
        baud_rate=_state.baud_rate,
        disk_dir=_state.disk_dir,
    )


@app.put("/config", response_model=ConfigResponse)
def update_config(req: ConfigRequest) -> ConfigResponse:
    changes: list[str] = []
    if req.serial_port is not None:
        changes.append(f"serial_port={req.serial_port!r}")
        _state.serial_port = req.serial_port
    if req.baud_rate is not None:
        changes.append(f"baud_rate={req.baud_rate}")
        _state.baud_rate = req.baud_rate
    if req.disk_dir is not None:
        changes.append(f"disk_dir={req.disk_dir!r}")
        _state.disk_dir = req.disk_dir
    if changes:
        log.info("Configuration updated: %s", ", ".join(changes))
    return get_config()


@app.delete("/disk")
def reset_disk() -> dict[str, str]:
    """Wipe the in-memory disk image back to blank."""
    _state.disk = DiskImage.blank(_state.model)
    log.info("Disk image reset to blank")
    return {"status": "ok", "detail": "Disk image reset to blank."}


@app.get("/ports", response_model=PortListResponse)
def list_ports() -> PortListResponse:
    """Return all available serial ports and flag FTDI candidates.

    Useful when auto-discovery fails and the user needs to call PUT /config
    to specify a port manually.
    """
    ports = list_all_ports()
    return PortListResponse(
        ports=[_port_info_to_response(p) for p in ports],
        ftdi_candidates=[p.device for p in ports if p.is_ftdi],
    )
