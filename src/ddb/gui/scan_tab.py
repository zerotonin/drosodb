"""Scan tab — camera preview on the left, vial detail on the right.

Start/Stop controls the FrameGrabber thread. Every decoded payload triggers
a DB lookup via `vial_detail` and repaints the right-hand panel.
"""

from __future__ import annotations

from datetime import UTC
from pathlib import Path

from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from sqlmodel import Session, select

from ddb.config import settings
from ddb.db import engine
from ddb.lineage import export_lineage_csv
from ddb.models import Vial
from ddb.reports import VialDetail, vial_detail
from ddb.scanner.payload import PayloadParseError, parse_payload

from .camera_widget import CameraWidget
from .dialogs import CreateVialDialog
from .frame_grabber import FrameGrabber


class DetailPanel(QWidget):
    """The right-hand panel. All fields update when a new vial is loaded."""

    def __init__(self) -> None:
        super().__init__()
        self._current_vial_id: int | None = None
        self._current_print_code: str | None = None

        self.header = QLabel("<i>No vial scanned yet.</i>")
        self.header.setTextFormat(Qt.TextFormat.RichText)

        form = QFormLayout()
        self.print_code_lbl = QLabel("-")
        self.status_lbl = QLabel("-")
        self.generation_lbl = QLabel("-")
        self.genotype_lbl = QLabel("-")
        self.notation_lbl = QLabel("-")
        self.notation_lbl.setWordWrap(True)
        self.notation_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.phenotype_lbl = QLabel("-")
        self.owner_lbl = QLabel("-")
        self.unit_lbl = QLabel("-")
        self.donor_lbl = QLabel("-")
        self.created_lbl = QLabel("-")
        self.lineage_lbl = QLabel("-")
        form.addRow("Print code:", self.print_code_lbl)
        form.addRow("Status:", self.status_lbl)
        form.addRow("Generation:", self.generation_lbl)
        form.addRow("Genotype:", self.genotype_lbl)
        form.addRow("Notation:", self.notation_lbl)
        form.addRow("Phenotype:", self.phenotype_lbl)
        form.addRow("Owner:", self.owner_lbl)
        form.addRow("Org unit:", self.unit_lbl)
        form.addRow("Donor:", self.donor_lbl)
        form.addRow("Created:", self.created_lbl)
        form.addRow("Lineage:", self.lineage_lbl)
        form_box = QGroupBox("Vial")
        form_box.setLayout(form)

        self.audit = QTextEdit(readOnly=True)
        self.audit.setMaximumHeight(160)
        audit_box = QGroupBox("Audit trail")
        audit_l = QVBoxLayout()
        audit_l.addWidget(self.audit)
        audit_box.setLayout(audit_l)

        btns = QHBoxLayout()
        self.flip_btn = QPushButton("Flip")
        self.decommission_btn = QPushButton("Decommission")
        self.print_btn = QPushButton("Print")
        self.export_lineage_btn = QPushButton("Export lineage CSV…")
        for b in (self.flip_btn, self.decommission_btn, self.print_btn, self.export_lineage_btn):
            b.setEnabled(False)
            btns.addWidget(b)
        self.export_lineage_btn.clicked.connect(self._export_lineage)
        self.print_btn.clicked.connect(self._print_label)

        layout = QVBoxLayout(self)
        layout.addWidget(self.header)
        layout.addWidget(form_box)
        layout.addWidget(audit_box)
        layout.addLayout(btns)
        layout.addStretch()

    @Slot(object, str)
    def show_detail(self, detail: VialDetail | None, raw: str) -> None:
        if detail is None:
            self.header.setText(
                f"<b>Unknown payload</b> — not in this database:<br><code>{raw}</code>"
            )
            self._clear_fields()
            return

        r = detail.row
        status = "ACTIVE" if r.is_active else "decommissioned"
        self.header.setText(f"<h2>{r.print_code}</h2>")
        self.print_code_lbl.setText(r.print_code)
        self.status_lbl.setText(status)
        self.generation_lbl.setText(str(r.generation))
        self.genotype_lbl.setText(r.genotype_name)
        self.notation_lbl.setText(r.genotype_notation)
        self.phenotype_lbl.setText(r.phenotype or "-")
        self.owner_lbl.setText(f"{r.owner_username or '-'} ({r.owner_full_name or '-'})")
        self.unit_lbl.setText(r.org_unit_name or "-")
        self.donor_lbl.setText(f"{r.donor_name or '-'}  strain#{r.donor_strain_id or '-'}")
        self.created_lbl.setText(r.created_at.isoformat(timespec="seconds"))

        lineage_parts: list[str] = []
        if detail.parent_flip_print_code:
            lineage_parts.append(f"← {detail.parent_flip_print_code}")
        if detail.parent_cross_print_codes:
            lineage_parts.append("× " + ", ".join(detail.parent_cross_print_codes))
        if detail.child_flip_print_codes:
            lineage_parts.append("→ " + ", ".join(detail.child_flip_print_codes))
        self.lineage_lbl.setText(" · ".join(lineage_parts) if lineage_parts else "-")

        lines = []
        for a in detail.audit:
            lines.append(
                f"{a.created_at.isoformat(timespec='seconds')}  "
                f"{a.action:<14} by {a.actor_username or '-'}"
            )
        self.audit.setPlainText("\n".join(lines) or "(no audit events)")

        self._current_vial_id = r.vial_id
        self._current_print_code = r.print_code
        self.export_lineage_btn.setEnabled(True)
        self.flip_btn.setEnabled(r.is_active)
        self.decommission_btn.setEnabled(r.is_active)
        # Print only if the label PNG still exists AND printer is enabled.
        label_path = settings.data_dir / "labels" / f"{r.print_code}.png"
        self.print_btn.setEnabled(settings.printer_enabled and label_path.exists())

    def _clear_fields(self) -> None:
        for lbl in (
            self.print_code_lbl,
            self.status_lbl,
            self.generation_lbl,
            self.genotype_lbl,
            self.notation_lbl,
            self.phenotype_lbl,
            self.owner_lbl,
            self.unit_lbl,
            self.donor_lbl,
            self.created_lbl,
            self.lineage_lbl,
        ):
            lbl.setText("-")
        self.audit.clear()
        self._current_vial_id = None
        self._current_print_code = None
        for b in (self.flip_btn, self.decommission_btn, self.print_btn, self.export_lineage_btn):
            b.setEnabled(False)

    def _print_label(self) -> None:
        if self._current_print_code is None:
            return
        label_path = settings.data_dir / "labels" / f"{self._current_print_code}.png"
        if not label_path.exists():
            QMessageBox.warning(
                self,
                "Label missing",
                f"No label PNG at {label_path}.\nRe-create or flip the vial to regenerate it.",
            )
            return
        # Lazy import so the GUI module still imports when brother_ql is absent.
        from ddb.printing.service import PrinterError, print_png

        self.print_btn.setEnabled(False)
        try:
            result = print_png(label_path.read_bytes())
        except PrinterError as e:
            QMessageBox.critical(self, "Printer error", str(e))
            self.print_btn.setEnabled(True)
            return
        except (OSError, ConnectionError) as e:
            QMessageBox.critical(self, "Printer unreachable", str(e))
            self.print_btn.setEnabled(True)
            return
        QMessageBox.information(self, "Printed", result.summary())
        self.print_btn.setEnabled(True)

    def _export_lineage(self) -> None:
        if self._current_vial_id is None:
            return
        with Session(engine) as s:
            v = s.get(Vial, self._current_vial_id)
            if v is None:
                return
            suggested = f"lineage_{v.print_code}.csv"
            out, _ = QFileDialog.getSaveFileName(self, "Save lineage CSV", suggested, "CSV (*.csv)")
            if not out:
                return
            path = export_lineage_csv(s, v.id, Path(out))
        QMessageBox.information(self, "Lineage exported", f"Wrote {path}")


class ScanTab(QWidget):
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
        controls.addWidget(self.sharpness_lbl)
        controls.addSpacing(12)
        controls.addWidget(self.status)

        self.camera = CameraWidget()
        self.camera.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.detail = DetailPanel()
        self.detail.setMaximumWidth(460)

        row = QHBoxLayout()
        row.addWidget(self.camera, 2)
        row.addWidget(self.detail, 1)

        outer = QVBoxLayout(self)
        outer.addLayout(controls)
        outer.addLayout(row)

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
            v = s.exec(select(Vial).where(Vial.print_code == parsed.print_code)).first()
            detail = vial_detail(s, v.id) if v else None
        if detail is None:
            QMessageBox.information(
                self, "Not found", f"No vial with print code {code!r} in the database."
            )
            return
        self.detail.show_detail(detail, synthetic)
        self.status.setText(f"loaded {code} by manual entry")
        self.code_edit.clear()

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

    @Slot(str)
    def _on_payload(self, raw: str) -> None:
        try:
            parsed = parse_payload(raw)
        except PayloadParseError:
            self.status.setText(f"ignored non-ddb payload: {raw[:40]}")
            return

        # Look up by print_code — works for both compact (`DDB:<pc>`) and
        # legacy (`ddb:1:vial:<id>?pc=<pc>&db=<db>`) payloads.
        with Session(engine) as s:
            v = s.exec(select(Vial).where(Vial.print_code == parsed.print_code)).first()
            if v is None and parsed.vial_id is not None:
                v = s.get(Vial, parsed.vial_id)  # legacy-flipped code fallback
            detail = vial_detail(s, v.id) if v else None

        self.detail.show_detail(detail, raw)
        if detail is None:
            self.status.setText(f"scanned {parsed.print_code} — not in DB")
        else:
            self.status.setText(f"last scan: {parsed.print_code}")

    @Slot(str)
    def _on_error(self, msg: str) -> None:
        QMessageBox.warning(self, "Scanner error", msg)
        self.status.setText(f"error: {msg}")

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

    def _open_new_vial_dialog(self) -> None:
        dlg = CreateVialDialog(self)
        if dlg.exec() != dlg.DialogCode.Accepted or dlg.result is None:
            return
        r = dlg.result
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
