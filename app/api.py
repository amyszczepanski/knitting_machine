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
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from PIL import Image
from pydantic import BaseModel

from app.brother_format import DiskImage, MachineModel
from app.image import ImageError, load_image

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

# ---------------------------------------------------------------------------
# Application & CORS
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Knitting Machine API",
    description="Brother KH-940 pattern management and upload.",
    version="0.1.0",
)

app.mount("/static", StaticFiles(directory="app/static"), name="static")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten in production
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Application state
# ---------------------------------------------------------------------------


class _AppState:
    """Single-process mutable state.  Not thread-safe for concurrent
    pattern writes; good enough for a single-user machine-side tool."""

    def __init__(self) -> None:
        self.model: MachineModel = MachineModel.KH940
        self.disk: DiskImage = DiskImage.blank(self.model)
        self.serial_port: str = "/dev/ttyUSB0"
        self.baud_rate: int = 9600
        self.disk_dir: str = "/tmp/knitting_disk"
        self.tasks: dict[str, "_TaskState"] = {}


_state = _AppState()


class _TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"


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


class ConfigRequest(BaseModel):
    serial_port: str | None = None
    baud_rate: int | None = None
    disk_dir: str | None = None


class ConfigResponse(BaseModel):
    serial_port: str
    baud_rate: int
    disk_dir: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _bytes_from_upload(upload: UploadFile) -> bytes:
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


def _run_send(task_id: str, disk_image_bytes: bytes) -> None:
    """Background thread: load disk image into the emulator and serve it.

    This function contains all direct hardware interaction and is the
    primary target for operational logging.  Every major step is logged
    at INFO so that a post-mortem of the log file can pinpoint exactly
    how far a transfer got before failing.
    """
    task = _state.tasks[task_id]
    task.status = _TaskStatus.RUNNING

    port = _state.serial_port
    baud = _state.baud_rate
    image_size = len(disk_image_bytes)

    log.info(
        "Send task %s started — port=%s  baud=%d  image=%d bytes",
        task_id,
        port,
        baud,
        image_size,
    )

    try:
        # ---- step 1: import serial emulator --------------------------------
        # Done here (not at module level) so that pyserial is only required
        # when actually sending.
        log.info("[%s] Importing serial emulator", task_id)
        from app.serial_emulator import PDDEmulator  # noqa: PLC0415
        import tempfile  # noqa: PLC0415

        # ---- step 2: create emulator and load disk image -------------------
        log.info("[%s] Creating PDDEmulator and loading disk image", task_id)
        with tempfile.TemporaryDirectory() as tmpdir:
            emulator = PDDEmulator(disk_dir=tmpdir)
            emulator.load_disk_image(disk_image_bytes)
            log.info(
                "[%s] Disk image loaded into emulator (%d bytes)", task_id, image_size
            )

            # ---- step 3: open serial port and begin serving ----------------
            log.info(
                "[%s] Opening serial port %s at %d baud — waiting for machine",
                task_id,
                port,
                baud,
            )
            emulator.run(port=port, baudrate=baud)
            # run() is blocking until the machine finishes reading or the
            # user calls stop().  Reaching this line means it returned cleanly.
            log.info("[%s] emulator.run() returned — transfer complete", task_id)

        task.status = _TaskStatus.DONE
        log.info("[%s] Send task finished successfully", task_id)

    except Exception as exc:
        task.status = _TaskStatus.ERROR
        task.error = str(exc)
        # exc_info=True attaches the full traceback to the log record so the
        # exact line inside the emulator (or pyserial) that failed is visible.
        log.error("[%s] Send task failed: %s", task_id, exc, exc_info=True)


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
) -> WritePatternResponse:
    """Upload an image and write it as a knitting pattern.

    The image is scaled to ≤ 200 stitches wide, stretched vertically by
    stitch_aspect_ratio to correct for non-square stitch proportions,
    binarised, and encoded into the Brother disk image format.  The pattern
    can then be sent to the machine via POST /send.
    """
    raw = _bytes_from_upload(file)
    try:
        result = load_image(
            raw,
            threshold=threshold,
            stitch_aspect_ratio=stitch_aspect_ratio,
            max_rows=_state.disk.max_rows,
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
) -> PreviewResponse:
    """Return a scaled/binarised preview PNG without writing to disk."""
    raw = _bytes_from_upload(file)
    try:
        result = load_image(
            raw,
            threshold=threshold,
            stitch_aspect_ratio=stitch_aspect_ratio,
            max_rows=_state.disk.max_rows,
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
    """Spawn a background thread to serve the disk image over serial.

    Returns a task_id; poll GET /send/{task_id} for status.
    """
    task_id = str(uuid.uuid4())
    _state.tasks[task_id] = _TaskState()

    disk_bytes = _state.disk.to_disk_image_bytes()

    log.info(
        "Queuing send task %s — %d pattern(s) in disk image, port=%s",
        task_id,
        sum(1 for n in range(901, 1000) if _state.disk.read_pattern(n)),
        _state.serial_port,
    )

    thread = threading.Thread(
        target=_run_send,
        args=(task_id, disk_bytes),
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
