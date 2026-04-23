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
"""

from __future__ import annotations

import subprocess
import sys
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


class ReconnectChoice(StrEnum):
    PROCEED = "proceed"  # Printer is OK; caller can go ahead.
    SKIP = "skip"  # Caller should not print.
    CANCEL = "cancel"  # Caller should abort entirely.


# Inline bond-removal script. Runs non-interactively under /usr/bin/python3
# (plain stdlib subprocess — no pexpect, no interactive bluetoothctl).
#
# Starts with a bluetoothd health probe so a wedged daemon is surfaced
# as a specific exit code (6) and the UI can offer 'Restart BT service'
# instead of chasing a subsequent cryptic error.
_REMOVE_BOND_SCRIPT = r'''
import subprocess, sys, time

MAC = sys.argv[1]


def probe_bluetoothd():
    """Return (ok, diagnostic). A freshly-restarted bluetoothd can take
    1-2 s before it accepts DBus clients, so we retry a few times."""
    last = None
    for _ in range(5):
        try:
            p = subprocess.run(
                ["bluetoothctl", "list"],
                capture_output=True, text=True, timeout=4,
            )
        except subprocess.TimeoutExpired as e:
            last = ("TIMEOUT", str(e), None)
            time.sleep(1.0)
            continue
        combined = p.stdout + p.stderr
        if (
            p.returncode == 0
            and "Waiting to connect to bluetoothd" not in combined
            and "assertion" not in combined.lower()
        ):
            return True, combined
        last = (p.stdout, p.stderr, p.returncode)
        time.sleep(1.0)
    return False, last


ok, info = probe_bluetoothd()
if not ok:
    sys.stderr.write(
        "\nbluetoothd on this host is not accepting connections "
        "(wedged after an earlier bluetoothctl crash).\n"
        "Click 'Restart BT service' in the dialog, or run "
        "'sudo systemctl restart bluetooth' in a terminal, then retry.\n"
        "\n--- diagnostic ---\n"
    )
    if isinstance(info, tuple):
        sys.stderr.write("bluetoothctl probe returncode=" + repr(info[2]) + "\n")
        sys.stderr.write("stdout: " + repr(info[0])[:400] + "\n")
        sys.stderr.write("stderr: " + repr(info[1])[:400] + "\n")
    sys.exit(6)

# Drop the bond. Non-interactive `bluetoothctl remove MAC` exits 0 on
# success, nonzero on a true failure. "Device does not exist" is already
# the desired end state, so treat that as success too.
try:
    proc = subprocess.run(
        ["bluetoothctl", "remove", MAC],
        capture_output=True, text=True, timeout=10,
    )
except subprocess.TimeoutExpired:
    sys.stderr.write("\nbluetoothctl remove timed out\n")
    sys.exit(7)

combined = proc.stdout + proc.stderr
if proc.returncode == 0 or "Device has been removed" in combined \
        or "Device does not exist" in combined or "not available" in combined:
    sys.stdout.write(combined)
    sys.exit(0)

sys.stderr.write(combined)
sys.exit(proc.returncode or 1)
'''


def _looks_like_daemon_wedged(tail: str) -> bool:
    """Recognise the 'bluetoothd is wedged' signature in stderr tails.

    Triggered by (a) our script exiting with rc=6 (health-check line),
    and (b) bluetoothctl itself printing 'Waiting to connect to
    bluetoothd' / a dbus assertion before crashing. In both cases the
    only fix is restarting the bluetooth service."""
    t = tail.lower()
    return (
        "not accepting connections" in t
        or "waiting to connect to bluetoothd" in t
        or ("dbus" in t and "assertion" in t)
    )


class _RemoveBondWorker(QThread):
    """Runs the bond-removal script off the GUI thread."""

    finished_ok = Signal(bool, str)  # (success, last-stderr-or-stdout)

    def __init__(self, mac: str) -> None:
        super().__init__()
        self.mac = mac

    def run(self) -> None:
        try:
            proc = subprocess.run(
                [sys.executable, "-c", _REMOVE_BOND_SCRIPT, self.mac],
                capture_output=True,
                timeout=30,
                check=False,
            )
            if proc.returncode == 0:
                tail = proc.stdout.decode(errors="replace")[-400:]
            else:
                tail = proc.stderr.decode(errors="replace")[-800:]
            self.finished_ok.emit(proc.returncode == 0, tail)
        except Exception as e:  # noqa: BLE001
            self.finished_ok.emit(False, f"{type(e).__name__}: {e}")


class _BtServiceRestartWorker(QThread):
    """Restart the bluetooth service, then wait for it to come up.

    Uses plain `systemctl restart bluetooth` rather than `pkexec`: systemd's
    polkit action (`org.freedesktop.systemd1.manage-units`) is
    `auth_admin_keep`, so the desktop's polkit agent caches the password
    for ~5 minutes. `pkexec` re-prompts on every call because its own
    action `org.freedesktop.policykit.exec` is plain `auth_admin`.

    If systemctl reports 'Interactive authentication required' (no polkit
    agent available — e.g. SSH session), fall back to `pkexec` so the
    user still gets a prompt, just a noisier one.
    """

    finished_ok = Signal(bool, str)

    @staticmethod
    def _run_systemctl() -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            ["systemctl", "restart", "bluetooth"],
            capture_output=True,
            timeout=30,
            check=False,
        )

    @staticmethod
    def _run_pkexec() -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            ["pkexec", "systemctl", "restart", "bluetooth"],
            capture_output=True,
            timeout=60,
            check=False,
        )

    @staticmethod
    def _probe_once(timeout_s: float = 4.0) -> tuple[bool, str]:
        try:
            p = subprocess.run(
                ["bluetoothctl", "list"],
                capture_output=True, text=True, timeout=timeout_s,
            )
        except subprocess.TimeoutExpired:
            return False, "bluetoothctl timed out"
        combined = p.stdout + p.stderr
        if p.returncode == 0 and "Waiting to connect" not in combined:
            return True, combined.strip()
        return False, f"rc={p.returncode}: {combined.strip()[-200:]}"

    def _wait_for_daemon(self, seconds: float = 8.0) -> tuple[bool, str]:
        import time as _time

        last_msg = ""
        steps = int(seconds * 2)
        for _ in range(steps):
            _time.sleep(0.5)
            ok, msg = self._probe_once()
            last_msg = msg
            if ok:
                return True, "bluetoothd is accepting connections."
        return False, last_msg or "no bluetoothctl output"

    def _rfkill_cycle(self) -> bool:
        """Kernel-level kick for a wedged USB/HCI adapter.
        `rfkill` is usually group-accessible (plugdev / netdev) without
        sudo on Ubuntu, so this adds no extra password prompt. Harmless
        if the adapter wasn't actually wedged."""
        import time as _time

        try:
            subprocess.run(["rfkill", "block", "bluetooth"], timeout=5, check=False)
            _time.sleep(1.0)
            subprocess.run(["rfkill", "unblock", "bluetooth"], timeout=5, check=False)
        except FileNotFoundError:
            return False
        except Exception:  # noqa: BLE001
            return False
        return True

    def run(self) -> None:
        try:
            proc = self._run_systemctl()
            out = (proc.stdout + proc.stderr).decode(errors="replace")
            if proc.returncode != 0 and (
                "Interactive authentication required" in out
                or "Failed to connect to bus" in out
            ):
                # No polkit agent in this session — fall back to pkexec.
                proc = self._run_pkexec()
                out = (proc.stdout + proc.stderr).decode(errors="replace")
            if proc.returncode != 0:
                self.finished_ok.emit(False, out.strip()[-400:])
                return

            ok, msg = self._wait_for_daemon(seconds=8.0)
            if ok:
                self.finished_ok.emit(True, msg)
                return

            # Service is running but bluetoothctl still can't talk to it.
            # That signature matches a kernel-level wedged HCI (seen on
            # USB dongles after 'Failed to set mode: 0x03'). Kick the
            # adapter with rfkill and re-probe.
            if self._rfkill_cycle():
                ok2, msg2 = self._wait_for_daemon(seconds=6.0)
                if ok2:
                    self.finished_ok.emit(
                        True, "Recovered via rfkill cycle. " + msg2
                    )
                    return
                self.finished_ok.emit(
                    False,
                    "systemctl restart + rfkill cycle both ran, but "
                    "bluetoothctl still can't reach the daemon. "
                    "Last probe: " + msg2 + "\n\n"
                    "Physical fix: unplug and replug the Bluetooth "
                    "adapter (or reboot).",
                )
            else:
                self.finished_ok.emit(
                    False,
                    "Service restarted but bluetoothctl still can't "
                    "connect, and rfkill isn't available. Last probe: "
                    + msg,
                )
        except FileNotFoundError as e:
            self.finished_ok.emit(
                False,
                f"{e.filename} not found — run this in a terminal instead:\n"
                "    sudo systemctl restart bluetooth",
            )
        except Exception as e:  # noqa: BLE001
            self.finished_ok.emit(False, f"{type(e).__name__}: {e}")


class PrinterReconnectDialog(QDialog):
    def __init__(self, status: PrinterStatus, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Printer not reachable")
        self.setModal(True)
        self.choice: ReconnectChoice = ReconnectChoice.CANCEL

        self._status = status
        self._reset: _RemoveBondWorker | None = None

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

    def _on_reset_bond(self) -> None:
        mac = settings.printer_bluetooth_mac
        if not mac:
            QMessageBox.warning(self, "No MAC configured", "Set DDB_PRINTER_BLUETOOTH_MAC.")
            return
        self._set_busy("Resetting BT bond…")
        self._reset = _RemoveBondWorker(mac)
        self._reset.finished_ok.connect(self._on_reset_done)
        self._reset.start()

    def _on_reset_done(self, ok: bool, tail: str) -> None:
        if not ok:
            self._set_busy(None)
            self._render_message()
            if _looks_like_daemon_wedged(tail):
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
        self._bt_restart = _BtServiceRestartWorker()
        self._bt_restart.finished_ok.connect(self._on_restart_done)
        self._bt_restart.start()

    def _on_restart_done(self, ok: bool, tail: str) -> None:
        self._set_busy(None)
        self._render_message()
        if ok:
            QMessageBox.information(
                self,
                "Bluetooth restarted",
                "Bluetooth service restarted and the daemon is responding. "
                "Click 'Re-pair Bluetooth' (or 'Guided steps…' if the "
                "printer's BT also needs cycling).\n\n" + tail.strip()[-200:],
            )
        else:
            # Bigger dialog with the full diagnostic — we're out of
            # clean-software tricks at this point and the user needs to
            # see what happened.
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
# When plain Re-pair keeps failing — typically because the printer's own
# Bluetooth stack got wedged — the user needs a repeatable recipe:
# power-cycle BT on the printer, give the stack time to come back,
# *then* run the re-pair. This dialog walks them through it with
# printer-specific instructions and a countdown for each wait step,
# and ends by firing the same _RemoveBondWorker as the quick path.


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
        self._reset: _RemoveBondWorker | None = None
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
        self._reset = _RemoveBondWorker(self._mac)
        self._reset.finished_ok.connect(self._on_reset_done)
        self._reset.start()

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
        if _looks_like_daemon_wedged(tail):
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
