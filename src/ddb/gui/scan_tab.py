"""Scan tab — camera preview on the left, vial detail on the right.

Start/Stop controls the FrameGrabber thread. Every decoded payload
triggers a DB lookup via `lookup_detail_by_payload` and repaints the
right-hand panel. The detail panel itself (with all of its per-vial
buttons) lives in `ddb.gui.vial_detail_panel` so this tab stays focused
on scanning controls and status rendering.
"""

from __future__ import annotations

from datetime import UTC
from pathlib import Path

from PySide6.QtCore import Slot
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
from sqlmodel import Session

from ddb.config import settings
from ddb.db import engine
from ddb.reports import vial_detail
from ddb.scanner.lookup import lookup_detail_by_payload
from ddb.scanner.payload import PayloadParseError, parse_payload

from .camera_widget import CameraWidget
from .dialogs import CreateVialDialog
from .frame_grabber import FrameGrabber
from .printer_status import PrinterStatusLight, shared_monitor
from .sounds import play_scan_sound
from .vial_detail_panel import DetailPanel


class ScanTab(QWidget):
    """Scan-mode tab: camera controls + printer light + vial detail panel.

    Owns the FrameGrabber lifecycle. All DB lookups and label printing
    live in helpers (`lookup_detail_by_payload`, `DetailPanel`); this
    class is just the Qt glue.
    """

    def __init__(self) -> None:
        super().__init__()
        self._grabber: FrameGrabber | None = None

        self.role_box = QComboBox()
        self.role_box.addItems(["back", "front"])
        # Honour the persisted default — user can still switch per-session.
        default_idx = self.role_box.findText(settings.default_camera_role)
        if default_idx >= 0:
            self.role_box.setCurrentIndex(default_idx)
        self.start_btn = QPushButton("Start")
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setEnabled(False)
        self.new_vial_btn = QPushButton("+ New vial")
        # Debug-only: save the current frame + decode report to disk.
        # Toggle visibility with DDB_GUI_DEBUG=1 in the .env until the
        # Settings tab exposes a checkbox for it.
        self.snapshot_btn = QPushButton("Save snapshot")
        self.snapshot_btn.setEnabled(False)
        self.snapshot_btn.setVisible(settings.gui_debug)
        self.status = QLabel("stopped.")
        # Live sharpness readout — traffic-light coded so the user can tune
        # label distance without eyeballing the preview.
        self.sharpness_lbl = QLabel("sharpness —")
        self.sharpness_lbl.setToolTip(
            "Variance-of-Laplacian blur metric.\n"
            "  >300 → decode likely\n"
            "  100–300 → borderline\n"
            "  <100 → too blurry (move label 30–50 cm away)"
        )
        self._set_sharpness(None)

        # Manual type-in fallback — when the camera can't decode, the user
        # can read the 5-char print code off the label with their eyes and
        # enter it here to jump straight to the vial's detail panel.
        self.code_edit = QLineEdit()
        self.code_edit.setPlaceholderText("print code")
        self.code_edit.setMaximumWidth(120)
        self.code_edit.setToolTip(
            "Type the alphanumeric code from the label (e.g. VWM2D) and press "
            "Enter to load the vial. Useful when the QR doesn't scan."
        )
        self.lookup_btn = QPushButton("Lookup")

        self.start_btn.clicked.connect(self._start)
        self.stop_btn.clicked.connect(self._stop)
        self.new_vial_btn.clicked.connect(self._open_new_vial_dialog)
        self.snapshot_btn.clicked.connect(self._save_snapshot)
        self.lookup_btn.clicked.connect(self._manual_lookup)
        self.code_edit.returnPressed.connect(self._manual_lookup)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("Camera role:"))
        controls.addWidget(self.role_box)
        controls.addWidget(self.start_btn)
        controls.addWidget(self.stop_btn)
        controls.addSpacing(16)
        controls.addWidget(QLabel("Code:"))
        controls.addWidget(self.code_edit)
        controls.addWidget(self.lookup_btn)
        controls.addSpacing(16)
        controls.addWidget(self.new_vial_btn)
        if settings.gui_debug:
            controls.addSpacing(12)
            controls.addWidget(self.snapshot_btn)
        controls.addStretch()
        # Printer status indicator — attaches to the shared monitor so
        # the dot updates every poll cycle. Clicking the dot forces an
        # immediate re-probe (handy when the printer was off a moment ago).
        self.printer_light = PrinterStatusLight(show_text=True)
        monitor = shared_monitor()
        if monitor is not None:
            self.printer_light.attach(monitor)
            self.printer_light.clicked.connect(monitor.force_probe)
        controls.addWidget(self.printer_light)
        controls.addSpacing(12)
        controls.addWidget(self.sharpness_lbl)
        controls.addSpacing(12)
        controls.addWidget(self.status)

        self.camera = CameraWidget()
        self.camera.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.detail = DetailPanel()
        self.detail.setMaximumWidth(460)
        self.detail.vial_changed.connect(self.status.setText)

        row = QHBoxLayout()
        row.addWidget(self.camera, 2)
        row.addWidget(self.detail, 1)

        outer = QVBoxLayout(self)
        outer.addLayout(controls)
        outer.addLayout(row)

    # ------------------------------------------------------------------
    # FrameGrabber lifecycle
    # ------------------------------------------------------------------

    def _start(self) -> None:
        if self._grabber is not None:
            return
        role = self.role_box.currentText()
        # Auto-snapshot on sharpness peaks when debug mode is on, so the
        # user can tune distance empirically — peaks are hard to catch by
        # hand-clicking a button.
        auto_dir = settings.data_dir / "snapshots" / "auto" if settings.gui_debug else None
        self._grabber = FrameGrabber(role, auto_snapshot_dir=auto_dir)
        self._grabber.frame_ready.connect(self.camera.on_frame)
        self._grabber.payload_decoded.connect(self._on_payload)
        self._grabber.sharpness_changed.connect(self._set_sharpness)
        self._grabber.auto_snapshot_saved.connect(self._on_auto_snapshot)
        self._grabber.error.connect(self._on_error)
        self._grabber.finished.connect(self._on_grabber_finished)
        self._grabber.start()
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.role_box.setEnabled(False)
        self.snapshot_btn.setEnabled(True)
        self.status.setText(f"scanning with role {role!r}…")

    def _stop(self) -> None:
        if self._grabber is None:
            return
        self._grabber.stop()
        self._grabber.wait(2000)

    @Slot()
    def _on_grabber_finished(self) -> None:
        self._grabber = None
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.role_box.setEnabled(True)
        self.snapshot_btn.setEnabled(False)
        self._set_sharpness(None)
        self.status.setText("stopped.")

    @Slot(str)
    def _on_auto_snapshot(self, path: str) -> None:
        """Tell the user (briefly) that the grabber auto-saved a sharpness peak."""
        self.status.setText(f"peak frame saved: {Path(path).name}")

    # ------------------------------------------------------------------
    # Payload → vial detail
    # ------------------------------------------------------------------

    def _manual_lookup(self) -> None:
        """Load a vial from a hand-typed print code (QR-scanner fallback)."""
        code = self.code_edit.text().strip().upper()
        if not code:
            return
        synthetic = f"DDB:{code}"  # same shape as a compact QR payload
        try:
            parsed = parse_payload(synthetic)
        except PayloadParseError as e:
            QMessageBox.warning(self, "Bad code", f"{code!r} is not a valid print code: {e}")
            return
        with Session(engine) as s:
            detail = lookup_detail_by_payload(s, parsed)
        if detail is None:
            QMessageBox.information(
                self, "Not found", f"No vial with print code {code!r} in the database."
            )
            return
        self.detail.show_detail(detail, synthetic)
        self.status.setText(f"loaded {code} by manual entry")
        self.code_edit.clear()

    @Slot(str)
    def _on_payload(self, raw: str) -> None:
        try:
            parsed = parse_payload(raw)
        except PayloadParseError:
            self.status.setText(f"ignored non-ddb payload: {raw[:40]}")
            return

        # Audible confirmation a QR was decoded — fires regardless of
        # whether the code is in the DB, because the user wants to hear
        # "the camera saw it" even when scanning an unknown label.
        if settings.scan_sound:
            play_scan_sound()

        with Session(engine) as s:
            detail = lookup_detail_by_payload(s, parsed)

        self.detail.show_detail(detail, raw)
        if detail is None:
            self.status.setText(f"scanned {parsed.print_code} — not in DB")
        else:
            self.status.setText(f"last scan: {parsed.print_code}")

    @Slot(str)
    def _on_error(self, msg: str) -> None:
        QMessageBox.warning(self, "Scanner error", msg)
        self.status.setText(f"error: {msg}")

    @Slot(float)
    def _set_sharpness(self, value: float | None) -> None:
        """Paint the sharpness readout red/amber/green by threshold."""
        if value is None:
            self.sharpness_lbl.setText("sharpness —")
            self.sharpness_lbl.setStyleSheet("color: #888;")
            return
        if value >= 300:
            color = "#0a8a0a"  # green
        elif value >= 100:
            color = "#b8860b"  # amber
        else:
            color = "#b00020"  # red
        self.sharpness_lbl.setText(f"sharpness {value:,.0f}")
        self.sharpness_lbl.setStyleSheet(f"color: {color}; font-weight: bold;")

    # ------------------------------------------------------------------
    # Debug snapshot
    # ------------------------------------------------------------------

    def _save_snapshot(self) -> None:
        """Debug: dump the current frame + run every decoder on it.

        Shown only when DDB_GUI_DEBUG=1. Writes the PNG under
        data/snapshots/<timestamp>.png so it survives the session and can
        be attached to a bug report.
        """
        if self._grabber is None:
            return
        frame = self._grabber.latest_frame()
        if frame is None:
            QMessageBox.warning(self, "No frame yet", "Camera hasn't produced a frame.")
            return

        from datetime import datetime

        import cv2

        # Underscore-prefixed helpers are internal but imported here
        # intentionally so the dialog reports exactly what each decoder saw.
        from ddb.scanner.decoder import (
            _ZXING_AVAILABLE,
            _decode_opencv,
            _decode_zxing,
        )

        out_dir = settings.data_dir / "snapshots"
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        out_path = out_dir / f"scan_{stamp}.png"
        cv2.imwrite(str(out_path), frame)

        zxing_hits = _decode_zxing(frame) if _ZXING_AVAILABLE else []
        opencv_hits = _decode_opencv(frame)

        # Sharpness — variance of Laplacian. <100 = blurry, >300 = sharp enough.
        # Fixed-focus webcams only resolve modules above ~300; that's the
        # threshold we hint at below.
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())

        # Was the QR at least DETECTED (finder patterns) even if not decoded?
        det = cv2.QRCodeDetector()
        finder_ok, _ = det.detect(frame)

        lines = [
            f"Saved: {out_path}",
            f"Size : {frame.shape[1]}×{frame.shape[0]} (BGR)",
            f"Sharpness: {sharpness:.0f}   (>300 = decodable, <100 = blurry)",
            f"QR finder patterns located: {'yes' if finder_ok else 'no'}",
            "",
            (
                f"zxing-cpp : {len(zxing_hits)} hit(s)  {zxing_hits}"
                if _ZXING_AVAILABLE
                else "zxing-cpp : (not installed)"
            ),
            f"opencv    : {len(opencv_hits)} hit(s)  {opencv_hits}",
        ]
        if not zxing_hits and not opencv_hits:
            lines.append("")
            if sharpness < 100:
                lines.append("Frame is out of focus. This USB webcam is fixed-focus;")
                lines.append("hold the label 30–50 cm away (not closer).")
            elif finder_ok:
                lines.append("QR detected but modules can't be resolved — either")
                lines.append("slightly too blurry or too small in frame. Try a bit")
                lines.append("further away with better lighting.")
            else:
                lines.append("No QR finder patterns visible. Check:")
                lines.append("  • label is in view (use the green guide square)")
                lines.append("  • QR is not obscured by fingers / reflections")
                lines.append("  • correct camera role is selected")
        QMessageBox.information(self, "Snapshot saved", "\n".join(lines))

    # ------------------------------------------------------------------
    # New-vial dialog
    # ------------------------------------------------------------------

    def _open_new_vial_dialog(self) -> None:
        dlg = CreateVialDialog(self)
        if dlg.exec() != dlg.DialogCode.Accepted or dlg.result is None:
            return
        r = dlg.result
        batch = len(r.batch_print_codes) or 1
        if batch > 1:
            print_suffix = (
                f" [{r.printed_count}/{batch} printed"
                + (f", {r.failed_count} failed" if r.failed_count else "")
                + "]"
                if r.printed_count or r.failed_count
                else ""
            )
            self.status.setText(
                f"created {batch} × {r.genotype_name}: "
                f"{', '.join(r.batch_print_codes)}{print_suffix}"
            )
        else:
            suffix = " [printed]" if r.printed else ""
            self.status.setText(
                f"created {r.print_code} ({r.genotype_name}) — label: {r.label_path}{suffix}"
            )
        # Pre-load the detail panel with the new vial so the user can
        # verify immediately without scanning.
        with Session(engine) as s:
            detail = vial_detail(s, r.vial_id)
        synthetic_payload = f"new vial {r.print_code}"
        self.detail.show_detail(detail, synthetic_payload)

    def shutdown(self) -> None:
        """Called by MainWindow on closeEvent so the thread exits cleanly."""
        self._stop()
