"""Create-vial dialog — the "New Vial" button in the Scan tab opens this.

Thin wrapper over `ddb.workflows.create_vial`. All the interesting logic
(print-code generation, audit, label rendering) lives in the backend.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
)
from sqlmodel import Session, select

from ddb.config import settings
from ddb.db import engine
from ddb.models import Genotype, User
from ddb.workflows import WorkflowError, create_vial


@dataclass
class CreateVialResult:
    vial_id: int
    print_code: str
    label_path: str
    genotype_name: str
    printed: bool = False
    print_message: str | None = None


class CreateVialDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("New vial")
        self.setModal(True)
        self.result: CreateVialResult | None = None

        self.genotype_box = QComboBox()
        self.genotype_box.setEditable(True)  # type-to-search
        self.genotype_box.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)

        self.owner_box = QComboBox()
        # No "(none)" option — an owner is always required. "Who owns this
        # stock?" is something a biologist should never skip answering,
        # and leaving it unset means the label has no @user on it.

        self.notes_edit = QLineEdit()
        self.notes_edit.setPlaceholderText("Optional — e.g. 'fresh flip from stock'")

        self.print_chk = QCheckBox("Print label after creating")
        self.print_chk.setChecked(settings.printer_enabled and settings.printer_auto_print)
        self.print_chk.setEnabled(settings.printer_enabled)
        if not settings.printer_enabled:
            self.print_chk.setToolTip(
                "Enable DDB_PRINTER_ENABLED=1 (and configure the backend) in your .env."
            )

        form = QFormLayout()
        form.addRow("Genotype:", self.genotype_box)
        form.addRow("Owner:", self.owner_box)
        form.addRow("Notes:", self.notes_edit)
        form.addRow("", self.print_chk)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self._on_ok)
        self.buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.buttons)

        self._load_choices()
        self.genotype_box.setFocus(Qt.FocusReason.OtherFocusReason)

    def _load_choices(self) -> None:
        with Session(engine) as s:
            genos = s.exec(select(Genotype).order_by(Genotype.name)).all()
            users = s.exec(select(User).order_by(User.username)).all()
        for g in genos:
            self.genotype_box.addItem(g.name, userData=g.id)
        for u in users:
            label = f"{u.username} — {u.full_name or ''}".strip(" —")
            self.owner_box.addItem(label, userData=u.id)
        if not users:
            # Offer a clear hint instead of a silent empty dropdown.
            self.owner_box.addItem("(no users — run `ddb seed-demo`)", userData=None)
            self.owner_box.setEnabled(False)

    def _selected_genotype_id(self) -> int | None:
        """Prefer the combo's userData; fall back to matching the typed name."""
        idx = self.genotype_box.currentIndex()
        data = self.genotype_box.itemData(idx) if idx >= 0 else None
        if data is not None and self.genotype_box.currentText() == self.genotype_box.itemText(idx):
            return int(data)
        # User typed something — look it up by exact name.
        typed = self.genotype_box.currentText().strip()
        if not typed:
            return None
        with Session(engine) as s:
            g = s.exec(select(Genotype).where(Genotype.name == typed)).first()
            return g.id if g else None

    def _on_ok(self) -> None:
        geno_id = self._selected_genotype_id()
        if geno_id is None:
            QMessageBox.warning(self, "Missing genotype", "Pick a genotype from the list.")
            return
        owner_id = self.owner_box.currentData()
        if owner_id is None:
            QMessageBox.warning(self, "Missing owner", "Pick an owner for the vial.")
            return
        notes = self.notes_edit.text().strip() or None

        try:
            with Session(engine) as s:
                created = create_vial(
                    s,
                    genotype_id=geno_id,
                    actor_id=owner_id,
                    owner_id=owner_id,
                    notes=notes,
                )
                geno = s.get(Genotype, geno_id)
                geno_name = geno.name if geno else ""
        except WorkflowError as e:
            QMessageBox.critical(self, "Could not create vial", str(e))
            return

        printed = False
        print_message: str | None = None
        if self.print_chk.isChecked():
            # Lazy import so the dialog still loads when brother_ql is absent.
            from ddb.printing.service import PrinterError, print_png

            try:
                result = print_png(created.label_path.read_bytes())
                printed = True
                print_message = result.summary()
            except (PrinterError, OSError, ConnectionError) as e:
                # Don't fail the whole create — the vial exists, label is
                # on disk, the user can hit Print again from DetailPanel.
                print_message = f"Print failed: {e}"
                QMessageBox.warning(self, "Print failed", print_message)

        self.result = CreateVialResult(
            vial_id=created.vial.id,
            print_code=created.vial.print_code,
            label_path=str(created.label_path),
            genotype_name=geno_name,
            printed=printed,
            print_message=print_message,
        )
        self.accept()
