"""High-level print service — glue between config, raster, and backend."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ddb.config import settings
from ddb.printing.backends import (
    BluetoothRFCOMMBackend,
    FileBackend,
    NetworkTCPBackend,
    PrintBackend,
)
from ddb.printing.raster import build_raster
from ddb.printing.status import StatusBlock


class PrinterError(RuntimeError):
    """Raised when the printer reports an error, or transport fails."""


@dataclass(frozen=True)
class PrintResult:
    backend_description: str
    raster_bytes: int
    blocks: list[StatusBlock]

    @property
    def last(self) -> StatusBlock | None:
        return self.blocks[-1] if self.blocks else None

    def summary(self) -> str:
        if not self.blocks:
            return f"{self.backend_description}  bytes={self.raster_bytes}  (no status replies)"
        tail = "  |  ".join(b.describe() for b in self.blocks)
        return f"{self.backend_description}  bytes={self.raster_bytes}  [{tail}]"


def _build_bluetooth() -> PrintBackend:
    if not settings.printer_bluetooth_mac:
        raise PrinterError("printer_backend=bluetooth but DDB_PRINTER_BLUETOOTH_MAC is unset")
    return BluetoothRFCOMMBackend(
        mac=settings.printer_bluetooth_mac,
        channel=settings.printer_bluetooth_channel,
        system_python=settings.printer_system_python,
    )


def _build_network() -> PrintBackend:
    if not settings.printer_network_host:
        raise PrinterError("printer_backend=network but DDB_PRINTER_NETWORK_HOST is unset")
    return NetworkTCPBackend(
        host=settings.printer_network_host,
        port=settings.printer_network_port,
    )


def _build_file() -> PrintBackend:
    out_dir = settings.printer_file_dir or (settings.data_dir / "print_jobs")
    return FileBackend(out_dir=Path(out_dir))


# Registry of configured-backend builders. Adding a new backend is two
# steps: implement `PrintBackend` and add an entry here — no if/elif in
# `get_backend` to touch.
_BACKENDS: dict[str, Callable[[], PrintBackend]] = {
    "bluetooth": _build_bluetooth,
    "network": _build_network,
    "file": _build_file,
}


def get_backend() -> PrintBackend:
    """Construct the configured backend, raising PrinterError if misconfigured."""
    kind = settings.printer_backend.lower()
    builder = _BACKENDS.get(kind)
    if builder is None:
        raise PrinterError(f"unknown printer_backend={settings.printer_backend!r}")
    return builder()


def print_png(
    png_bytes: bytes,
    *,
    backend: PrintBackend | None = None,
    model: str | None = None,
    label: str | None = None,
    cut: bool = True,
    timeout: float = 30.0,
) -> PrintResult:
    """Build a raster for the configured printer and send it.

    Raises PrinterError if the resulting status blocks contain an error flag.
    """
    if not settings.printer_enabled and backend is None:
        raise PrinterError(
            "Printer is disabled in settings (DDB_PRINTER_ENABLED=0). "
            "Enable it or pass an explicit backend."
        )

    backend = backend or get_backend()
    raster = build_raster(
        png_bytes,
        model=model or settings.printer_model,
        label=label or settings.printer_label,
        cut=cut,
    )
    blocks = backend.send(raster, timeout=timeout)
    result = PrintResult(
        backend_description=backend.description,
        raster_bytes=len(raster),
        blocks=blocks,
    )
    err_block = next((b for b in blocks if b.is_error), None)
    if err_block is not None:
        raise PrinterError(
            f"Printer reported error: {err_block.describe()}. Full trace: {result.summary()}"
        )
    return result


def print_label_file(
    path: Path | str,
    *,
    backend: PrintBackend | None = None,
    **kwargs,
) -> PrintResult:
    """Thin wrapper for the common case: re-print an existing label PNG."""
    return print_png(Path(path).read_bytes(), backend=backend, **kwargs)
