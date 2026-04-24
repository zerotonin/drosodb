"""Printer-not-reachable dialog.

Offered to the user when a print-intent action (Print button, auto-print
on Create Vial) fires while the shared monitor says the printer isn't
OK. Buttons:

  Retry probe      — re-run the ESC i S probe now.
  Reset BT bond    — `bluetoothctl remove <MAC>` to drop a stale bond.
  Restart BT svc   — kick bluetoothd when it's wedged (polkit prompt).
  Guided steps…    — short wizard: power-cycle printer BT + reset bond.
  Skip printing    — caller should still create the DB row but not print.
  Cancel           — caller should abort the whole action.

Why we *remove* the bond instead of re-pairing: the QL-820NWB accepts
RFCOMM SPP connections on channel 1 without requiring authentication
("Just Works"). When BlueZ holds a stored link key, every connect
enforces authentication; if the printer and BlueZ ever disagree on the
key (firmware quirks, power cycles, earlier bluetoothctl crashes) the
kernel returns EACCES on connect. Dropping the bond lets the kernel
fall back to an unauthenticated SPP connection, which is exactly what
the printer wants.

The heavy lifting (subprocess orchestration, daemon probing, rfkill
escalation) lives in `ddb.printing.bt_recovery` as plain synchronous
functions — this module only wraps them in a QThread so they don't
block the UI, and wires results into Qt dialogs.
"""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

from ddb.config import settings
from ddb.gui.printer_status import (
    PrinterState,
    PrinterStatus,
    probe_printer,
    shared_monitor,
)
from ddb.printing.bt_recovery import (
    ActionResult,
    looks_like_daemon_wedged,
    remove_bond,
    restart_service,
)


class ReconnectChoice(StrEnum):
    PROCEED = "proceed"  # Printer is OK; caller can go ahead.
    SKIP = "skip"  # Caller should not print.
    CANCEL = "cancel"  # Caller should abort entirely.


class _BluetoothWorker(QThread):
    """Runs a 0-arg `ActionResult`-returning callable off the GUI thread.

    One class covers every async BT action (bond removal, service
    restart, future additions): the caller binds arguments with a
    lambda, we emit the result as a plain (ok, message) tuple so the
    Qt receiver stays simple.
    """

    finished_ok = Signal(bool, str)

    def __init__(self, fn: Callable[[], ActionResult]) -> None:
        super().__init__()
        self._fn = fn

    def run(self) -> None:
        try:
            result = self._fn()
            self.finished_ok.emit(bool(result.ok), str(result.message))
        except Exception as e:  # noqa: BLE001 — surface any failure to the UI instead of crashing the thread
            self.finished_ok.emit(False, f"{type(e).__name__}: {e}")


class PrinterReconnectDialog(QDialog):
    """Modal dialog offered when the background printer monitor is red.

    Owns the four recovery buttons plus a Guided-steps wizard entry
    point. All actual BlueZ work happens in `bt_recovery`; this class
    only renders state and dispatches worker threads.
    """

    def __init__(self, status: PrinterStatus, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Printer not reachable")
        self.setModal(True)
        self.choice: ReconnectChoice = ReconnectChoice.CANCEL

        self._status = status
        self._worker: _BluetoothWorker | None = None

        # While this dialog is open we have BlueZ traffic to the printer
        # under direct user control — if the background monitor also
        # probes at the same moment, bluetoothctl can crash with a D-Bus
        # error mid-remove. Pausing here guarantees a clean bus.
        monitor = shared_monitor()
        if monitor is not None:
            monitor.pause()
        self.finished.connect(self._on_finished)

        self.msg_lbl = QLabel()
        self.msg_lbl.setWordWrap(True)
        self.msg_lbl.setTextFormat(Qt.TextFormat.RichText)

        self.retry_btn = QPushButton("Retry probe")
        self.reset_btn = QPushButton("Reset BT bond")
        self.reset_btn.setToolTip(
            "Drop any stored pairing with the printer. The QL-820NWB accepts "
            "RFCOMM SPP without pairing, and a stale bond makes every connect "
            "fail with EACCES — so clearing the bond is the right reset."
        )
        self.restart_btn = QPushButton("Restart BT service")
        self.restart_btn.setToolTip(
            "Runs 'systemctl restart bluetooth' (polkit prompt). "
            "Use when bluetoothctl complains about 'Waiting to connect to "
            "bluetoothd' or a D-Bus assertion — the daemon is wedged."
        )
        self.guided_btn = QPushButton("Guided steps…")
        self.guided_btn.setToolTip(
            "Short wizard: power-cycle the printer's Bluetooth, then "
            "reset the bond. Use when Retry probe keeps failing."
        )
        self.skip_btn = QPushButton("Skip printing")
        self.cancel_btn = QPushButton("Cancel")
        if settings.printer_backend != "bluetooth" or not settings.printer_bluetooth_mac:
            self.reset_btn.setEnabled(False)
            self.guided_btn.setEnabled(False)
            tip = "Only available when the Bluetooth backend is configured with a MAC."
            self.reset_btn.setToolTip(tip)
            self.guided_btn.setToolTip(tip)

        self.retry_btn.clicked.connect(self._on_retry)
        self.reset_btn.clicked.connect(self._on_reset_bond)
        self.restart_btn.clicked.connect(self._on_restart_service)
        self.guided_btn.clicked.connect(self._on_guided)
        self.skip_btn.clicked.connect(self._on_skip)
        self.cancel_btn.clicked.connect(self._on_cancel)

        btns = QHBoxLayout()
        btns.addWidget(self.retry_btn)
        btns.addWidget(self.reset_btn)
        btns.addWidget(self.restart_btn)
        btns.addWidget(self.guided_btn)
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

    # ------------------------------------------------------------------
    # Rendering + busy-state
    # ------------------------------------------------------------------

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
        for b in (
            self.retry_btn,
            self.reset_btn,
            self.restart_btn,
            self.guided_btn,
            self.skip_btn,
            self.cancel_btn,
        ):
            b.setEnabled(not busy)
        if not busy and (
            settings.printer_backend != "bluetooth" or not settings.printer_bluetooth_mac
        ):
            self.reset_btn.setEnabled(False)
            self.guided_btn.setEnabled(False)

    def _start_worker(
        self, fn: Callable[[], ActionResult], done: Callable[[bool, str], None]
    ) -> None:
        """Kick off a background BT action. Keeps a reference to the
        thread so Qt doesn't GC it mid-run."""
        self._worker = _BluetoothWorker(fn)
        self._worker.finished_ok.connect(done)
        self._worker.start()

    # ------------------------------------------------------------------
    # Button handlers
    # ------------------------------------------------------------------

    def _on_retry(self) -> None:
        self._set_busy("Probing printer…")
        QTimer.singleShot(50, self._do_retry)

    def _do_retry(self) -> None:
        status = probe_printer()
        self._status = status
        self._set_busy(None)
        self._render_message()
        m = shared_monitor()
        if m is not None:
            m.force_probe()
        if status.state is PrinterState.OK:
            self.choice = ReconnectChoice.PROCEED
            self.accept()

    def _on_reset_bond(self) -> None:
        mac = settings.printer_bluetooth_mac
        if not mac:
            QMessageBox.warning(self, "No MAC configured", "Set DDB_PRINTER_BLUETOOTH_MAC.")
            return
        self._set_busy("Resetting BT bond…")
        self._start_worker(lambda: remove_bond(mac), self._on_reset_done)

    def _on_reset_done(self, ok: bool, tail: str) -> None:
        if not ok:
            self._set_busy(None)
            self._render_message()
            if looks_like_daemon_wedged(tail):
                self._prompt_restart_service(tail)
                return
            QMessageBox.warning(
                self, "Reset failed", tail.strip() or "Unknown error — see logs."
            )
            return
        self._set_busy("Bond cleared. Probing printer…")
        QTimer.singleShot(500, self._do_retry)

    def _prompt_restart_service(self, tail: str) -> None:
        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Icon.Warning)
        msg.setWindowTitle("Bluetooth daemon wedged")
        msg.setText(
            "The Bluetooth daemon on this computer isn't responding. "
            "Restart it now? (polkit will ask for your password.)"
        )
        msg.setDetailedText(tail.strip())
        msg.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if msg.exec() == QMessageBox.StandardButton.Yes:
            self._on_restart_service()

    def _on_restart_service(self) -> None:
        self._set_busy("Restarting the Bluetooth service (polkit prompt)…")
        self._start_worker(restart_service, self._on_restart_done)

    def _on_restart_done(self, ok: bool, tail: str) -> None:
        self._set_busy(None)
        self._render_message()
        if ok:
            QMessageBox.information(
                self,
                "Bluetooth restarted",
                "Bluetooth service restarted and the daemon is responding. "
                "Click 'Reset BT bond' (or 'Guided steps…' if the "
                "printer's BT also needs cycling).\n\n" + tail.strip()[-200:],
            )
        else:
            # Out of software tricks — show the user the diagnostic and
            # point at physical recovery.
            msg = QMessageBox(self)
            msg.setIcon(QMessageBox.Icon.Critical)
            msg.setWindowTitle("Restart didn't clear the wedge")
            msg.setText(
                "The Bluetooth adapter is still unresponsive after "
                "restarting the service. The most reliable fix from "
                "here is to physically unplug and replug the Bluetooth "
                "adapter (or reboot the machine)."
            )
            msg.setDetailedText(tail.strip() or "No additional diagnostic output.")
            msg.setStandardButtons(QMessageBox.StandardButton.Ok)
            msg.exec()

    def _on_guided(self) -> None:
        mac = settings.printer_bluetooth_mac
        if not mac:
            QMessageBox.warning(self, "No MAC configured", "Set DDB_PRINTER_BLUETOOTH_MAC.")
            return
        wiz = GuidedReconnectDialog(mac, self)
        wiz.exec()
        if wiz.reset_ok:
            self._set_busy("Probing printer…")
            QTimer.singleShot(500, self._do_retry)

    def _on_skip(self) -> None:
        self.choice = ReconnectChoice.SKIP
        self.accept()

    def _on_cancel(self) -> None:
        self.choice = ReconnectChoice.CANCEL
        self.reject()

    def _on_finished(self, _result: int) -> None:
        # Resume monitor AFTER closing — and kick it to probe now so the
        # main window's dot reflects the outcome of whatever we just did.
        monitor = shared_monitor()
        if monitor is not None:
            monitor.resume()
            monitor.force_probe()


# ----------------------------------------------------------------------
# Guided reconnect wizard
# ----------------------------------------------------------------------
#
# When Retry / Reset keep failing — typically because the printer's own
# Bluetooth stack got wedged — the user needs a repeatable recipe:
# power-cycle BT on the printer, give the stack time to come back,
# then reset the bond. This dialog walks them through it with printer-
# specific instructions and a countdown on each wait step.


_GUIDE_STEPS: list[tuple[str, str, int]] = [
    (
        "1 of 4 — Turn Bluetooth off on the printer",
        "On the QL-820NWB's LCD:\n\n"
        "   Menu  →  Bluetooth  →  Bluetooth (On/Off)  →  Off  →  OK\n\n"
        "The Bluetooth icon in the status bar should disappear.\n\n"
        "Click <b>Next</b> when that's done.",
        0,
    ),
    (
        "2 of 4 — Wait for the stack to settle",
        "Give the printer a few seconds with Bluetooth off so its BT "
        "controller fully resets. Next unlocks automatically.",
        5,
    ),
    (
        "3 of 4 — Turn Bluetooth back on",
        "On the printer's LCD:\n\n"
        "   Menu  →  Bluetooth  →  Bluetooth (On/Off)  →  On  →  OK\n\n"
        "Watch for the Bluetooth icon to reappear in the status bar.\n\n"
        "Click <b>Next</b> once you see it.",
        0,
    ),
    (
        "4 of 4 — Clear the bond and re-test",
        "Ready. Click <b>Reset bond</b> to drop any stale pairing from "
        "this computer. The printer will then be reachable as an "
        "unpaired SPP device, which is exactly what it expects.",
        0,
    ),
]


class GuidedReconnectDialog(QDialog):
    """Step-by-step guide that ends by running the bond-removal script.

    The outer `PrinterReconnectDialog` already handles pausing the
    background monitor while any reconnect dialog is open, so we don't
    need to touch the monitor here.
    """

    def __init__(self, mac: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Guided reconnect")
        self.setModal(True)
        self.setMinimumWidth(520)
        self._mac = mac
        self._step = 0
        self._wait_remaining = 0
        self._worker: _BluetoothWorker | None = None
        self.reset_ok = False

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick_countdown)

        self.title_lbl = QLabel()
        self.title_lbl.setStyleSheet("font-weight: bold; font-size: 14px;")
        self.body_lbl = QLabel()
        self.body_lbl.setWordWrap(True)
        self.body_lbl.setTextFormat(Qt.TextFormat.RichText)
        self.countdown_lbl = QLabel()
        self.countdown_lbl.setStyleSheet("color: #666;")

        hr = QFrame()
        hr.setFrameShape(QFrame.Shape.HLine)
        hr.setFrameShadow(QFrame.Shadow.Sunken)

        self.spinner = QProgressBar()
        self.spinner.setRange(0, 0)
        self.spinner.hide()
        self.tail_lbl = QLabel()
        self.tail_lbl.setWordWrap(True)
        self.tail_lbl.setStyleSheet("color: #a00; font-family: monospace;")
        self.tail_lbl.hide()

        self.back_btn = QPushButton("Back")
        self.next_btn = QPushButton("Next")
        self.run_btn = QPushButton("Reset bond")
        self.run_btn.hide()
        self.close_btn = QPushButton("Close")
        self.back_btn.clicked.connect(self._on_back)
        self.next_btn.clicked.connect(self._on_next)
        self.run_btn.clicked.connect(self._on_run)
        self.close_btn.clicked.connect(self.reject)

        btns = QHBoxLayout()
        btns.addWidget(self.back_btn)
        btns.addStretch()
        btns.addWidget(self.run_btn)
        btns.addWidget(self.next_btn)
        btns.addWidget(self.close_btn)

        lo = QVBoxLayout(self)
        lo.addWidget(self.title_lbl)
        lo.addWidget(self.body_lbl)
        lo.addWidget(self.countdown_lbl)
        lo.addWidget(hr)
        lo.addWidget(self.spinner)
        lo.addWidget(self.tail_lbl)
        lo.addLayout(btns)

        self._render_step()

    # --- step rendering ------------------------------------------------

    def _render_step(self) -> None:
        title, body, wait_s = _GUIDE_STEPS[self._step]
        self.title_lbl.setText(title)
        self.body_lbl.setText(body)
        self.back_btn.setEnabled(self._step > 0)

        is_last = self._step == len(_GUIDE_STEPS) - 1
        self.next_btn.setVisible(not is_last)
        self.run_btn.setVisible(is_last)
        self.run_btn.setEnabled(is_last)

        if wait_s > 0:
            self._wait_remaining = wait_s
            self.next_btn.setEnabled(False)
            self.countdown_lbl.setText(f"Please wait {wait_s} s…")
            self._timer.start()
        else:
            self._timer.stop()
            self.countdown_lbl.clear()
            self.next_btn.setEnabled(True)

    def _tick_countdown(self) -> None:
        self._wait_remaining -= 1
        if self._wait_remaining <= 0:
            self._timer.stop()
            self.countdown_lbl.setText("Ready — click Next.")
            self.next_btn.setEnabled(True)
        else:
            self.countdown_lbl.setText(f"Please wait {self._wait_remaining} s…")

    # --- button handlers -----------------------------------------------

    def _on_back(self) -> None:
        if self._step > 0:
            self._step -= 1
            self._render_step()

    def _on_next(self) -> None:
        if self._step < len(_GUIDE_STEPS) - 1:
            self._step += 1
            self._render_step()

    def _on_run(self) -> None:
        for b in (self.back_btn, self.next_btn, self.run_btn, self.close_btn):
            b.setEnabled(False)
        self.spinner.show()
        self.tail_lbl.hide()
        self.countdown_lbl.setText("Clearing stored bond…")
        mac = self._mac
        self._worker = _BluetoothWorker(lambda: remove_bond(mac))
        self._worker.finished_ok.connect(self._on_reset_done)
        self._worker.start()

    def _on_reset_done(self, ok: bool, tail: str) -> None:
        self.spinner.hide()
        self.close_btn.setEnabled(True)
        if ok:
            self.reset_ok = True
            self.countdown_lbl.setText("Bond cleared.")
            QTimer.singleShot(600, self.accept)
            return
        # Failure: let the user start the guided steps over without
        # closing the wizard — the printer may still be coming up.
        self.back_btn.setEnabled(True)
        self.run_btn.setEnabled(True)
        if looks_like_daemon_wedged(tail):
            self.countdown_lbl.setText(
                "The Bluetooth daemon is wedged — close this wizard and "
                "click 'Restart BT service', then try again."
            )
        else:
            self.countdown_lbl.setText(
                "Reset failed. Go Back to step 1 and cycle the printer's "
                "Bluetooth again, or click Reset bond to retry."
            )
        self.tail_lbl.setText((tail or "Unknown error").strip()[-400:])
        self.tail_lbl.show()


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


def check_printer_or_ask(parent) -> ReconnectChoice:
    """Short-hand gate that reads the shared monitor itself.

    Previously every print-intent call-site duplicated this block:

        monitor = shared_monitor()
        if monitor is not None:
            choice = ensure_printer_or_ask(self, monitor.last_status)
            ...

    When the monitor is missing (printer disabled in settings), there
    is nothing useful to ask about — the caller should proceed and let
    the downstream print attempt raise if it fails. Callers interpret
    the returned PROCEED / SKIP / CANCEL according to whether they're
    gating a single print or a batch.
    """
    monitor = shared_monitor()
    if monitor is None:
        return ReconnectChoice.PROCEED
    return ensure_printer_or_ask(parent, monitor.last_status)
