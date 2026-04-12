"""
app/api.py — FastAPI application for the Brother KH-930E knitting machine.

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
"""

from __future__ import annotations

import base64
import io
import threading
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Annotated

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from PIL import Image
from pydantic import BaseModel

from app.brother_format import DiskImage
from app.image import ImageError, load_image

# ---------------------------------------------------------------------------
# Application & CORS
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Knitting Machine API",
    description="Brother KH-930E pattern management and upload.",
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
        self.disk: DiskImage = DiskImage.blank()
        self.serial_port: str = "/dev/ttyUSB0"
        self.baud_rate: int = 19200
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
    """Background thread: load disk image into the emulator and serve it."""
    task = _state.tasks[task_id]
    task.status = _TaskStatus.RUNNING
    try:
        # Import here so serial is only required when actually sending
        from app.serial_emulator import PDDEmulator

        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            emulator = PDDEmulator(disk_dir=tmpdir)
            emulator.load_disk_image(disk_image_bytes)
            # run() is blocking; the machine will read then the user stops it
            # For MVP we run until stop() is called externally.
            # The task is marked DONE only after run() returns.
            emulator.run(port=_state.serial_port, baudrate=_state.baud_rate)

        task.status = _TaskStatus.DONE
    except Exception as exc:
        task.status = _TaskStatus.ERROR
        task.error = str(exc)


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
            if pixel_rows:
                patterns.append(
                    PatternInfo(
                        number=number,
                        rows=len(pixel_rows),
                        stitches=len(pixel_rows[0]) if pixel_rows else 0,
                    )
                )
        except Exception:
            continue
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
) -> WritePatternResponse:
    """Upload an image and write it as a knitting pattern.

    The image is scaled to ≤ 200 stitches wide, binarised, and encoded
    into the Brother disk image format.  The pattern can then be sent to
    the machine via POST /send.
    """
    raw = _bytes_from_upload(file)
    try:
        result = load_image(raw, threshold=threshold)
    except ImageError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    try:
        _state.disk.write_pattern(number, result.rows)
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
) -> PreviewResponse:
    """Return a scaled/binarised preview PNG without writing to disk."""
    raw = _bytes_from_upload(file)
    try:
        result = load_image(raw, threshold=threshold)
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
    if req.serial_port is not None:
        _state.serial_port = req.serial_port
    if req.baud_rate is not None:
        _state.baud_rate = req.baud_rate
    if req.disk_dir is not None:
        _state.disk_dir = req.disk_dir
    return get_config()


@app.delete("/disk")
def reset_disk() -> dict[str, str]:
    """Wipe the in-memory disk image back to blank."""
    _state.disk = DiskImage.blank()
    return {"status": "ok", "detail": "Disk image reset to blank."}
