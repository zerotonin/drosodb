"""Printer transport backends: bluetooth, network, file.

Each backend accepts a raster byte stream and returns any status blocks the
printer sent back. Only the Bluetooth backend needs a hardware handshake; the
others are trivially testable.
"""

from typing import Protocol

from ddb.printing.backends.bluetooth import BluetoothRFCOMMBackend
from ddb.printing.backends.file import FileBackend
from ddb.printing.backends.network import NetworkTCPBackend
from ddb.printing.status import StatusBlock


class PrintBackend(Protocol):
    """Any class with this shape is a valid transport."""

    def send(self, raster: bytes, *, timeout: float = 30.0) -> list[StatusBlock]: ...

    @property
    def description(self) -> str: ...


__all__ = [
    "BluetoothRFCOMMBackend",
    "FileBackend",
    "NetworkTCPBackend",
    "PrintBackend",
]
