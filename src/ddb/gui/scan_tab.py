"""Scan tab — camera preview on the left, vial detail on the right.

Start/Stop controls the FrameGrabber thread. Every decoded payload triggers
a DB lookup via `vial_detail` and repaints the right-hand panel.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
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
        self.start_btn = QPushButton("Start")
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setEnabled(False)
        self.new_vial_btn = QPushButton("+ New vial")
        self.status = QLabel("stopped.")

        self.start_btn.clicked.connect(self._start)
        self.stop_btn.clicked.connect(self._stop)
        self.new_vial_btn.clicked.connect(self._open_new_vial_dialog)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("Camera role:"))
        controls.addWidget(self.role_box)
        controls.addWidget(self.start_btn)
        controls.addWidget(self.stop_btn)
        controls.addSpacing(24)
        controls.addWidget(self.new_vial_btn)
        controls.addStretch()
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
        self._grabber = FrameGrabber(role)
        self._grabber.frame_ready.connect(self.camera.on_frame)
        self._grabber.payload_decoded.connect(self._on_payload)
        self._grabber.error.connect(self._on_error)
        self._grabber.finished.connect(self._on_grabber_finished)
        self._grabber.start()
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.role_box.setEnabled(False)
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
        self.status.setText("stopped.")

    @Slot(str)
    def _on_payload(self, raw: str) -> None:
        try:
            parsed = parse_payload(raw)
        except PayloadParseError:
            self.status.setText(f"ignored non-ddb payload: {raw[:40]}")
            return

        with Session(engine) as s:
            # Fast lookup: the payload carries the vial id, so one session.get
            # is cheaper than a full search; vial_detail handles the detail
            # aggregation internally.
            v = s.exec(select(Vial).where(Vial.id == parsed.entity_id)).first()
            detail = vial_detail(s, v.id) if v else None

        self.detail.show_detail(detail, raw)
        self.status.setText(f"last scan: {raw}")

    @Slot(str)
    def _on_error(self, msg: str) -> None:
        QMessageBox.warning(self, "Scanner error", msg)
        self.status.setText(f"error: {msg}")

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
