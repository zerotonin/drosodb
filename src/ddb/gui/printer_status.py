"""Background printer-health poller + little indicator widget.

Design goals:
  * The Scan tab shows a traffic-light dot that reflects the printer's
    current state without the user having to click anything.
  * Any code about to talk to the printer can read the latest status
    cheaply (no blocking call) and, when it's red, offer the user a
    reconnect dialog instead of just failing the batch.
  * The probe itself is slow (BT connect + ESC i S + status-block read,
    ~1–3 s when OK, up to 30 s when the printer is unreachable), so it
    runs off the GUI thread.

The shared monitor lives on the `QApplication` instance and is picked
up by whichever widget wants to render / act on the state.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from enum import StrEnum

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QSizePolicy, QWidget

from ddb.config import settings

_MONITOR_PROPERTY = "ddb_printer_status_monitor"


class PrinterState(StrEnum):
    UNKNOWN = "unknown"  # No probe has run yet.
    DISABLED = "disabled"  # DDB_PRINTER_ENABLED=0.
    OK = "ok"  # Status reply = ready, no error bits.
    ERROR = "error"  # Status reply had err1/err2 set.
    UNREACHABLE = "unreachable"  # Couldn't open the transport at all.


@dataclass(frozen=True)
class PrinterStatus:
    state: PrinterState
    detail: str

    @property
    def is_ok(self) -> bool:
        return self.state is PrinterState.OK


# ------------------------------------------------------------------
# Probe
# ------------------------------------------------------------------


def probe_printer(timeout: float = 6.0) -> PrinterStatus:
    """Blocking one-shot: send ESC i S, classify the result.

    Meant to be called from a worker thread (the BT probe can block for
    10+ seconds if the printer is off). Safe to call at any time — if the
    printer is disabled in settings we short-circuit to DISABLED.
    """
    if not settings.printer_enabled:
        return PrinterStatus(PrinterState.DISABLED, "Printer disabled in settings")

    try:
        from ddb.printing.service import PrinterError, get_backend

        backend = get_backend()
    except Exception as e:  # noqa: BLE001 — mis-configured backend, user-facing
        return PrinterStatus(PrinterState.ERROR, f"Backend configuration: {e}")

    query = b"\x00" * 200 + b"\x1b\x40" + b"\x1b\x69\x53"
    try:
        blocks = backend.send(query, timeout=timeout)
    except ConnectionError as e:
        return PrinterStatus(PrinterState.UNREACHABLE, str(e))
    except PrinterError as e:
        return PrinterStatus(PrinterState.ERROR, str(e))
    except Exception as e:  # noqa: BLE001
        return PrinterStatus(PrinterState.ERROR, f"{type(e).__name__}: {e}")

    if not blocks:
        return PrinterStatus(PrinterState.UNREACHABLE, "Connected, but the printer did not reply")
    last = blocks[-1]
    if last.is_error:
        return PrinterStatus(PrinterState.ERROR, last.describe())
    return PrinterStatus(PrinterState.OK, last.describe())


# ------------------------------------------------------------------
# Monitor thread
# ------------------------------------------------------------------


class PrinterStatusMonitor(QThread):
    """Polls `probe_printer()` at a low rate and emits state changes.

    Exposes `force_probe()` to interrupt the current sleep and re-check
    immediately (used by the reconnect dialog's Retry button).
    """

    status_changed = Signal(object)  # PrinterStatus
    probe_started = Signal()
    probe_finished = Signal()

    def __init__(self, interval_s: float = 60.0, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.interval_s = interval_s
        self._stop = False
        self._wake = threading.Event()
        self._last_status = PrinterStatus(PrinterState.UNKNOWN, "No probe yet.")

    @property
    def last_status(self) -> PrinterStatus:
        return self._last_status

    def force_probe(self) -> None:
        self._wake.set()

    def stop(self) -> None:
        self._stop = True
        self._wake.set()

    def run(self) -> None:
        while not self._stop:
            self.probe_started.emit()
            status = probe_printer()
            self._last_status = status
            self.status_changed.emit(status)
            self.probe_finished.emit()
            # Interruptible sleep — the next iteration fires either after
            # `interval_s` elapses or when force_probe() is called.
            self._wake.wait(self.interval_s)
            self._wake.clear()


# ------------------------------------------------------------------
# Shared-instance plumbing
# ------------------------------------------------------------------


def shared_monitor() -> PrinterStatusMonitor | None:
    """Return the app-wide monitor, if one has been registered."""
    app = QApplication.instance()
    if app is None:
        return None
    monitor = app.property(_MONITOR_PROPERTY)
    return monitor if isinstance(monitor, PrinterStatusMonitor) else None


def install_shared_monitor(monitor: PrinterStatusMonitor) -> None:
    app = QApplication.instance()
    if app is None:
        raise RuntimeError("No QApplication instance — install after constructing it.")
    app.setProperty(_MONITOR_PROPERTY, monitor)


# ------------------------------------------------------------------
# Indicator widget
# ------------------------------------------------------------------


_STATE_STYLE: dict[PrinterState, tuple[str, str]] = {
    # (dot-color, short-label)
    PrinterState.OK: ("#0a8a0a", "printer ready"),
    PrinterState.ERROR: ("#b00020", "printer error"),
    PrinterState.UNREACHABLE: ("#b00020", "printer offline"),
    PrinterState.DISABLED: ("#888", "printer disabled"),
    PrinterState.UNKNOWN: ("#888", "checking…"),
}


class PrinterStatusLight(QWidget):
    """Tiny dot + optional text. Live-updates via a monitor signal."""

    clicked = Signal()

    def __init__(self, *, show_text: bool = True, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self._show_text = show_text
        self._dot = QLabel("●")
        self._dot.setStyleSheet("font-size: 18px; color: #888;")
        self._text = QLabel("printer …")
        self._text.setStyleSheet("color: #666;")

        lo = QHBoxLayout(self)
        lo.setContentsMargins(2, 0, 2, 0)
        lo.setSpacing(4)
        lo.addWidget(self._dot)
        if show_text:
            lo.addWidget(self._text)

    def attach(self, monitor: PrinterStatusMonitor) -> None:
        """Subscribe to a monitor's state updates and render the initial value."""
        monitor.status_changed.connect(self._render)
        self._render(monitor.last_status)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self.clicked.emit()
        super().mousePressEvent(event)

    def _render(self, status: PrinterStatus) -> None:
        color, label = _STATE_STYLE.get(status.state, ("#888", "printer …"))
        self._dot.setStyleSheet(f"font-size: 18px; color: {color};")
        if self._show_text:
            self._text.setText(label)
        self.setToolTip(f"{label}\n\n{status.detail}")
