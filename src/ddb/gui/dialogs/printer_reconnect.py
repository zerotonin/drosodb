"""Printer-not-reachable dialog.

Offered to the user when a print-intent action (Print button, auto-print
on Create Vial) fires while the shared monitor says the printer isn't
OK. Four buttons:

  Retry probe  — re-run the ESC i S probe now (takes a few seconds).
  Re-pair BT   — remove + re-pair the Brother bond, then probe again.
  Skip print   — caller should still create the DB row but not print.
  Cancel       — caller should abort the whole action.
"""

from __future__ import annotations

import subprocess
import sys
from enum import StrEnum

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

from ddb.config import settings
from ddb.gui.printer_status import PrinterState, PrinterStatus, probe_printer


class ReconnectChoice(StrEnum):
    PROCEED = "proceed"  # Printer is OK; caller can go ahead.
    SKIP = "skip"  # Caller should not print.
    CANCEL = "cancel"  # Caller should abort entirely.


# Inline re-pair script — runs under the conda env's Python, uses pexpect
# to drive bluetoothctl through remove → pair (auto-confirm passkey) →
# trust → connect.
_REPAIR_SCRIPT = r"""
import pexpect, sys, time

MAC = sys.argv[1]
bt = pexpect.spawn("bluetoothctl", encoding="utf-8", timeout=30)
bt.logfile_read = sys.stderr

def send(c):
    bt.sendline(c); time.sleep(0.3)

send(f"remove {MAC}")
bt.expect([r"Device has been removed", r"Device does not exist", pexpect.TIMEOUT], timeout=8)
send("power on")
send("agent NoInputNoOutput")
send("default-agent")
send(f"pair {MAC}")

for _ in range(6):
    idx = bt.expect(
        [
            r"Confirm passkey",
            r"Pairing successful",
            r"Failed to pair",
            pexpect.TIMEOUT,
            pexpect.EOF,
        ],
        timeout=25,
    )
    if idx == 0:
        send("yes")
    elif idx == 1:
        break
    else:
        sys.exit(1)

send(f"trust {MAC}")
time.sleep(0.5)
send(f"connect {MAC}")
time.sleep(2)
send("quit")
sys.exit(0)
"""


class _RepairWorker(QThread):
    finished_ok = Signal(bool, str)  # (success, last-stderr)

    def __init__(self, mac: str) -> None:
        super().__init__()
        self.mac = mac

    def run(self) -> None:
        try:
            proc = subprocess.run(
                [sys.executable, "-c", _REPAIR_SCRIPT, self.mac],
                capture_output=True,
                timeout=90,
                check=False,
            )
            tail = proc.stderr.decode(errors="replace")[-800:]
            self.finished_ok.emit(proc.returncode == 0, tail)
        except Exception as e:  # noqa: BLE001
            self.finished_ok.emit(False, f"{type(e).__name__}: {e}")


class PrinterReconnectDialog(QDialog):
    def __init__(self, status: PrinterStatus, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Printer not reachable")
        self.setModal(True)
        self.choice: ReconnectChoice = ReconnectChoice.CANCEL

        self._status = status
        self._repair: _RepairWorker | None = None

        self.msg_lbl = QLabel()
        self.msg_lbl.setWordWrap(True)
        self.msg_lbl.setTextFormat(Qt.TextFormat.RichText)

        self.retry_btn = QPushButton("Retry probe")
        self.repair_btn = QPushButton("Re-pair Bluetooth")
        self.skip_btn = QPushButton("Skip printing")
        self.cancel_btn = QPushButton("Cancel")
        if settings.printer_backend != "bluetooth" or not settings.printer_bluetooth_mac:
            self.repair_btn.setEnabled(False)
            self.repair_btn.setToolTip(
                "Only available when the Bluetooth backend is configured with a MAC."
            )

        self.retry_btn.clicked.connect(self._on_retry)
        self.repair_btn.clicked.connect(self._on_repair)
        self.skip_btn.clicked.connect(self._on_skip)
        self.cancel_btn.clicked.connect(self._on_cancel)

        btns = QHBoxLayout()
        btns.addWidget(self.retry_btn)
        btns.addWidget(self.repair_btn)
        btns.addStretch()
        btns.addWidget(self.skip_btn)
        btns.addWidget(self.cancel_btn)

        self.spinner = QProgressBar()
        self.spinner.setRange(0, 0)
        self.spinner.hide()

        layout = QVBoxLayout(self)
        layout.addWidget(self.msg_lbl)
        layout.addWidget(self.spinner)
        layout.addLayout(btns)

        self._render_message()

    def _render_message(self) -> None:
        headline = {
            PrinterState.UNREACHABLE: (
                "<b>Printer not reachable.</b> "
                "Check that it's powered on, within range, and not currently "
                "bonded to another host — the QL-820NWB only holds one BT "
                "connection at a time."
            ),
            PrinterState.ERROR: (
                "<b>Printer reported an error.</b> "
                "Check the printer's LCD for the blink pattern and media; "
                "ensure the tape door is closed."
            ),
            PrinterState.DISABLED: (
                "<b>Printer is disabled in settings.</b> "
                "Set <code>DDB_PRINTER_ENABLED=1</code> in .env and restart."
            ),
            PrinterState.UNKNOWN: "Printer status has not been determined yet.",
            PrinterState.OK: "Printer looks OK now.",
        }.get(self._status.state, str(self._status.state))
        self.msg_lbl.setText(f"{headline}<br><br><i>{self._status.detail}</i>")

    def _set_busy(self, message: str | None) -> None:
        busy = message is not None
        self.spinner.setVisible(busy)
        if busy:
            self.msg_lbl.setText(message)
        for b in (self.retry_btn, self.repair_btn, self.skip_btn, self.cancel_btn):
            b.setEnabled(not busy)
        if not busy and (
            settings.printer_backend != "bluetooth" or not settings.printer_bluetooth_mac
        ):
            self.repair_btn.setEnabled(False)

    def _on_retry(self) -> None:
        self._set_busy("Probing printer…")
        QTimer.singleShot(50, self._do_retry)

    def _do_retry(self) -> None:
        status = probe_printer()
        self._status = status
        self._set_busy(None)
        self._render_message()
        from ddb.gui.printer_status import shared_monitor

        m = shared_monitor()
        if m is not None:
            m.force_probe()
        if status.state is PrinterState.OK:
            self.choice = ReconnectChoice.PROCEED
            self.accept()

    def _on_repair(self) -> None:
        mac = settings.printer_bluetooth_mac
        if not mac:
            QMessageBox.warning(self, "No MAC configured", "Set DDB_PRINTER_BLUETOOTH_MAC.")
            return
        self._set_busy("Re-pairing Bluetooth (~15 s)…")
        self._repair = _RepairWorker(mac)
        self._repair.finished_ok.connect(self._on_repair_done)
        self._repair.start()

    def _on_repair_done(self, ok: bool, tail: str) -> None:
        if not ok:
            self._set_busy(None)
            self._render_message()
            QMessageBox.warning(self, "Re-pair failed", tail.strip() or "Unknown error — see logs.")
            return
        self._set_busy("Re-paired. Probing printer…")
        QTimer.singleShot(500, self._do_retry)

    def _on_skip(self) -> None:
        self.choice = ReconnectChoice.SKIP
        self.accept()

    def _on_cancel(self) -> None:
        self.choice = ReconnectChoice.CANCEL
        self.reject()


def ensure_printer_or_ask(parent, status: PrinterStatus) -> ReconnectChoice:
    """Show the reconnect dialog if `status` isn't OK; return the user's choice.

    Callers use this as a gate before any print-intent action. When the
    status is already OK, returns PROCEED without opening the dialog.
    """
    if status.state is PrinterState.OK:
        return ReconnectChoice.PROCEED
    dlg = PrinterReconnectDialog(status, parent)
    dlg.exec()
    return dlg.choice
