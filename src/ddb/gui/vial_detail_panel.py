"""Right-hand vial-detail widget used by the Scan tab.

Kept separate from ScanTab so the scanning-control surface (camera,
buttons, manual lookup) stays readable and the detail panel can be
reused elsewhere in the future. Every field updates via `show_detail`;
the widget owns the Print / Flip / Decommission / Export-lineage
buttons for whichever vial is currently loaded.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import (
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from sqlmodel import Session

from ddb.config import settings
from ddb.db import engine
from ddb.gui.dialogs import ReconnectChoice, check_printer_or_ask
from ddb.gui.printer_status import shared_monitor
from ddb.lineage import export_lineage_csv
from ddb.models import Vial
from ddb.reports import VialDetail


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

        # A single Print click treats SKIP and CANCEL the same — both
        # abort. Only PROCEED sends bytes.
        if check_printer_or_ask(self) is not ReconnectChoice.PROCEED:
            return

        # Lazy import so the GUI module still imports when brother_ql is absent.
        from ddb.printing.service import PrinterError, print_png

        self.print_btn.setEnabled(False)
        monitor = shared_monitor()
        try:
            result = print_png(label_path.read_bytes())
        except PrinterError as e:
            QMessageBox.critical(self, "Printer error", str(e))
            self.print_btn.setEnabled(True)
            if monitor is not None:
                monitor.force_probe()  # something went wrong — re-check
            return
        except (OSError, ConnectionError) as e:
            QMessageBox.critical(self, "Printer unreachable", str(e))
            self.print_btn.setEnabled(True)
            if monitor is not None:
                monitor.force_probe()
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
