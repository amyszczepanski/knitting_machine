"""
app/ports.py — Serial port discovery for the Brother KH-940 knitting machine.

FTDI chips (VID 0x0403) are used by the cable that connects the host machine
to the KH-940.  Discovery filters available serial ports by that VID and
returns the single unambiguous match, or raises PortDiscoveryError with
enough context for the caller to surface a useful message to the user.
"""

from __future__ import annotations

from dataclasses import dataclass

from serial.tools.list_ports import comports
from serial.tools.list_ports_common import ListPortInfo

# USB vendor ID assigned to FTDI Ltd.
_FTDI_VID: int = 0x0403


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PortInfo:
    """Structured description of a single serial port."""

    device: str
    description: str
    manufacturer: str | None
    vid: int | None
    pid: int | None
    serial_number: str | None

    @property
    def is_ftdi(self) -> bool:
        return self.vid == _FTDI_VID

    @classmethod
    def from_list_port_info(cls, p: ListPortInfo) -> PortInfo:
        return cls(
            device=p.device,
            description=p.description or "",
            manufacturer=p.manufacturer or None,
            vid=p.vid,
            pid=p.pid,
            serial_number=p.serial_number or None,
        )


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class PortDiscoveryError(Exception):
    """Raised when FTDI port discovery cannot identify a single port.

    Attributes
    ----------
    candidates:
        The ports that were found.  Empty when no FTDI ports are present;
        contains two or more entries when the result is ambiguous.
    all_ports:
        Every available serial port on the system, regardless of VID.
        Included so callers can present a complete list to the user.
    """

    def __init__(
        self,
        message: str,
        *,
        candidates: list[PortInfo],
        all_ports: list[PortInfo],
    ) -> None:
        super().__init__(message)
        self.candidates: list[PortInfo] = candidates
        self.all_ports: list[PortInfo] = all_ports


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def list_all_ports() -> list[PortInfo]:
    """Return every available serial port on the system."""
    return [PortInfo.from_list_port_info(p) for p in comports()]


def discover_ftdi_port() -> PortInfo:
    """Return the single FTDI serial port, or raise PortDiscoveryError.

    Raises
    ------
    PortDiscoveryError
        If zero or more than one FTDI port is found.  The exception carries
        the list of FTDI candidates and the full list of available ports so
        that callers can construct a helpful message for the user.
    """
    all_ports = list_all_ports()
    candidates = [p for p in all_ports if p.is_ftdi]

    if len(candidates) == 1:
        return candidates[0]

    if len(candidates) == 0:
        raise PortDiscoveryError(
            "No FTDI serial port found. Is the cable plugged in?",
            candidates=candidates,
            all_ports=all_ports,
        )

    raise PortDiscoveryError(
        f"Found {len(candidates)} FTDI serial ports; cannot select one automatically.",
        candidates=candidates,
        all_ports=all_ports,
    )
