"""Create-vial dialog — the "New Vial" button in the Scan tab opens this.

Thin wrapper over `ddb.workflows.create_vial`. All the interesting logic
(print-code generation, audit, label rendering) lives in the backend.
Defaults (org unit, stock keeper) are pulled from `Settings` so the
biologist only has to pick a genotype and hit OK in the common case.
"""

from __future__ import annotations

from dataclasses import dataclass, field

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
    QSpinBox,
    QVBoxLayout,
)
from sqlmodel import Session, select

from ddb.config import settings
from ddb.db import engine
from ddb.models import Genotype, OrgUnit, User
from ddb.workflows import WorkflowError, create_vial


@dataclass
class CreateVialResult:
    """Batch result — fields are always present for the LAST created vial
    so existing single-vial callers (e.g. ScanTab's detail panel) keep
    working unchanged. `batch_print_codes` holds every code created in
    the same OK click; its length is the total batch size (>=1)."""

    vial_id: int
    print_code: str
    label_path: str
    genotype_name: str
    printed: bool = False
    print_message: str | None = None
    batch_print_codes: list[str] = field(default_factory=list)
    printed_count: int = 0
    failed_count: int = 0


def _genotype_display(g: Genotype) -> str:
    """Combo label — includes donor strain id so users can type e.g. '9405'."""
    if g.donor_strain_id:
        return f"{g.name}    #{g.donor_strain_id}"
    return g.name


# Small pause between batch prints so the Brother's BT stack can fully
# close the prior RFCOMM socket before we open the next — without this we
# hit "[Errno 16] Device or resource busy" on most prints past the first.
_BATCH_PRINT_SETTLE_S = 1.0


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

        # --- Count: number of vials to create in this click ----------------
        # Each iteration gets its own print code + QR — same genotype/owner/unit.
        self.count_spin = QSpinBox()
        self.count_spin.setRange(1, 96)  # plenty for a normal flip day
        self.count_spin.setValue(1)
        self.count_spin.setToolTip(
            "Create this many vials at once — same genotype/owner/unit, "
            "each with its own print code and QR."
        )

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
        form.addRow("Count:", self.count_spin)
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

            # Dropped-from-stock genotypes don't appear in New Vial — the
            # user has consciously declared those gone and we shouldn't
            # tempt them into recreating a vial by accident.
            genos = s.exec(
                select(Genotype).where(Genotype.is_in_stock.is_(True)).order_by(Genotype.name)
            ).all()
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
        count = int(self.count_spin.value())

        # Lazy-import printing only when actually needed.
        print_png = None
        printer_error_cls: type | None = None
        do_print = self.print_chk.isChecked()
        if do_print:
            from ddb.printing.service import PrinterError as _PrinterError
            from ddb.printing.service import print_png as _print_png

            print_png = _print_png
            printer_error_cls = _PrinterError

            # Gate the batch on the printer monitor. If the printer is red,
            # open the reconnect dialog; the user's choice maps to:
            #   PROCEED → unchanged (do_print stays True)
            #   SKIP    → create vials but don't print this batch
            #   CANCEL  → abort the whole batch (no vials, no labels)
            from ddb.gui.dialogs.printer_reconnect import (
                ReconnectChoice,
                ensure_printer_or_ask,
            )
            from ddb.gui.printer_status import shared_monitor

            monitor = shared_monitor()
            if monitor is not None:
                choice = ensure_printer_or_ask(self, monitor.last_status)
                if choice is ReconnectChoice.CANCEL:
                    return
                if choice is ReconnectChoice.SKIP:
                    do_print = False

        import time as _time

        batch_codes: list[str] = []
        printed_count = 0
        failed_count = 0
        first_print_error: str | None = None
        last_created = None
        last_label_path: str = ""
        geno_name = ""

        try:
            for i in range(count):
                with Session(engine) as s:
                    created = create_vial(
                        s,
                        genotype_id=geno_id,
                        actor_id=owner_id,
                        owner_id=owner_id,
                        org_unit_id=org_unit_id,
                        notes=notes,
                    )
                    if not geno_name:
                        geno = s.get(Genotype, geno_id)
                        geno_name = geno.name if geno else ""
                batch_codes.append(created.vial.print_code)
                last_created = created
                last_label_path = str(created.label_path)

                if do_print and print_png is not None:
                    try:
                        print_png(created.label_path.read_bytes())
                        printed_count += 1
                    except (printer_error_cls, OSError, ConnectionError) as e:  # type: ignore[misc]
                        # Keep going through the batch on print failures —
                        # the DB rows are already there, the user can hit
                        # Print on the detail panel or re-run.
                        failed_count += 1
                        if first_print_error is None:
                            first_print_error = str(e)
                    # Settle between prints (but not after the last one).
                    if i < count - 1:
                        _time.sleep(_BATCH_PRINT_SETTLE_S)
        except WorkflowError as e:
            QMessageBox.critical(
                self,
                "Could not create vial",
                f"{e}\n\nCreated {len(batch_codes)} of {count} before the error.",
            )
            if not batch_codes:
                return
            # Partial success: fall through and report what did land.

        if failed_count > 0 and first_print_error is not None:
            msg = (
                f"{failed_count} of {count} labels failed to print.\n\n"
                f"First error: {first_print_error}\n\n"
                "Vials are in the database; you can re-print from the Scan tab's "
                "Detail panel."
            )
            QMessageBox.warning(self, "Print partially failed", msg)

        assert last_created is not None  # batch_codes is non-empty here
        self.result = CreateVialResult(
            vial_id=last_created.vial.id,
            print_code=last_created.vial.print_code,
            label_path=last_label_path,
            genotype_name=geno_name,
            printed=printed_count > 0,
            print_message=(
                None
                if not do_print
                else f"{printed_count}/{count} printed"
                + (f" ({failed_count} failed)" if failed_count else "")
            ),
            batch_print_codes=batch_codes,
            printed_count=printed_count,
            failed_count=failed_count,
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
