"""Create-vial dialog — the "New Vial" button in the Scan tab opens this.

Thin wrapper over `ddb.workflows.create_vial`. All the interesting logic
(print-code generation, audit, label rendering) lives in the backend.
Defaults (org unit, stock keeper) are pulled from `Settings` so the
biologist only has to pick a genotype and hit OK in the common case.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QCompleter,
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
from ddb.models import Genotype, OrgUnit, User
from ddb.workflows import WorkflowError, create_vial


@dataclass
class CreateVialResult:
    vial_id: int
    print_code: str
    label_path: str
    genotype_name: str
    printed: bool = False
    print_message: str | None = None


def _genotype_display(g: Genotype) -> str:
    """Combo label — includes donor strain id so users can type e.g. '9405'."""
    if g.donor_strain_id:
        return f"{g.name}    #{g.donor_strain_id}"
    return g.name


class CreateVialDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("New vial")
        self.setModal(True)
        self.result: CreateVialResult | None = None

        # --- Genotype: searchable by name OR donor strain id ---------------
        self.genotype_box = QComboBox()
        self.genotype_box.setEditable(True)
        self.genotype_box.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        # Substring-match autocomplete so typing "9405" finds "rutabaga  #9405".
        completer = QCompleter(self.genotype_box)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.genotype_box.setCompleter(completer)

        # --- Owner ----------------------------------------------------------
        self.owner_box = QComboBox()

        # --- Org unit -------------------------------------------------------
        self.unit_box = QComboBox()

        # --- Notes ----------------------------------------------------------
        self.notes_edit = QLineEdit()
        self.notes_edit.setPlaceholderText("Optional — e.g. 'fresh flip from stock'")

        # --- Auto-print toggle ---------------------------------------------
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
        form.addRow("Org unit:", self.unit_box)
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

    # ------------------------------------------------------------------
    # Data loading + defaults
    # ------------------------------------------------------------------

    def _load_choices(self) -> None:
        with Session(engine) as s:
            default_user = _upsert_default_user(s)
            default_unit = _upsert_default_org_unit(s)

            genos = s.exec(select(Genotype).order_by(Genotype.name)).all()
            users = s.exec(select(User).order_by(User.username)).all()
            units = s.exec(select(OrgUnit).order_by(OrgUnit.name)).all()

            default_user_id = default_user.id
            default_unit_id = default_unit.id

        for g in genos:
            self.genotype_box.addItem(_genotype_display(g), userData=g.id)

        for u in users:
            label = f"{u.username} — {u.full_name or ''}".strip(" —")
            self.owner_box.addItem(label, userData=u.id)
        # Preselect the stock keeper (or whatever `default_owner_username` maps to).
        idx = self.owner_box.findData(default_user_id)
        if idx >= 0:
            self.owner_box.setCurrentIndex(idx)

        for unit in units:
            self.unit_box.addItem(unit.name, userData=unit.id)
        idx = self.unit_box.findData(default_unit_id)
        if idx >= 0:
            self.unit_box.setCurrentIndex(idx)

    # ------------------------------------------------------------------
    # Resolvers
    # ------------------------------------------------------------------

    def _selected_genotype_id(self) -> int | None:
        """Resolve the combo's selection or typed value to a Genotype id.

        Priority:
          1. If the user picked a list entry, use its userData directly.
          2. Else, the typed text is first matched against donor_strain_id
             (so '9405' finds the BDSC-9405 strain).
          3. Falling back to exact genotype name match.
        """
        idx = self.genotype_box.currentIndex()
        data = self.genotype_box.itemData(idx) if idx >= 0 else None
        if data is not None and self.genotype_box.currentText() == self.genotype_box.itemText(idx):
            return int(data)

        typed = self.genotype_box.currentText().strip().lstrip("#")
        if not typed:
            return None

        with Session(engine) as s:
            by_donor = s.exec(select(Genotype).where(Genotype.donor_strain_id == typed)).first()
            if by_donor is not None:
                return by_donor.id
            by_name = s.exec(select(Genotype).where(Genotype.name == typed)).first()
            return by_name.id if by_name else None

    # ------------------------------------------------------------------
    # Submit
    # ------------------------------------------------------------------

    def _on_ok(self) -> None:
        geno_id = self._selected_genotype_id()
        if geno_id is None:
            QMessageBox.warning(
                self,
                "Missing genotype",
                "Pick a genotype from the list, or type its name / donor#id.",
            )
            return
        owner_id = self.owner_box.currentData()
        if owner_id is None:
            QMessageBox.warning(self, "Missing owner", "Pick an owner for the vial.")
            return
        org_unit_id = self.unit_box.currentData()  # may be None; workflow accepts it
        notes = self.notes_edit.text().strip() or None

        try:
            with Session(engine) as s:
                created = create_vial(
                    s,
                    genotype_id=geno_id,
                    actor_id=owner_id,
                    owner_id=owner_id,
                    org_unit_id=org_unit_id,
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


# ----------------------------------------------------------------------
# Default-user / default-org-unit upsert helpers
# ----------------------------------------------------------------------


def _upsert_default_user(session: Session) -> User:
    """Return the configured default user, creating it if it doesn't exist."""
    username = settings.default_owner_username
    user = session.exec(select(User).where(User.username == username)).first()
    if user is not None:
        return user
    user = User(
        username=username,
        full_name=settings.default_owner_full_name,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def _upsert_default_org_unit(session: Session) -> OrgUnit:
    name = settings.default_org_unit
    unit = session.exec(select(OrgUnit).where(OrgUnit.name == name)).first()
    if unit is not None:
        return unit
    unit = OrgUnit(name=name, description="Default stock unit.")
    session.add(unit)
    session.commit()
    session.refresh(unit)
    return unit
