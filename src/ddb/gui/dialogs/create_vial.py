"""Create-vial dialog — the "New Vial" button in the Scan tab opens this.

Thin wrapper over `ddb.workflows.create_vial`. All the interesting logic
(print-code generation, audit, label rendering) lives in the backend.
Defaults (org unit, stock keeper) are pulled from `Settings` so the
biologist only has to pick a genotype and hit OK in the common case.

`_on_ok` used to be a 130-line block that mixed form validation,
printer gating, a batch loop, error handling, and result-building.
It is now a four-step orchestrator:
    inputs   = _resolve_inputs()
    do_print = _gate_printer(inputs.do_print)
    outcome  = _run_batch(inputs, do_print=do_print)
    result   = _build_result(inputs, outcome, do_print=do_print)
Each step has a single responsibility and a dataclass for its I/O.
"""

from __future__ import annotations

import time
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
from ddb.current_user import current_user_id
from ddb.db import engine
from ddb.gui.dialogs.printer_reconnect import ReconnectChoice, check_printer_or_ask
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


@dataclass(frozen=True)
class _Inputs:
    """Validated values pulled off the form, ready for the workflow call."""

    genotype_id: int
    owner_id: int
    org_unit_id: int | None
    notes: str | None
    count: int
    do_print: bool


@dataclass
class _BatchOutcome:
    """Accumulator passed through the batch loop so per-iteration state
    and final-result state live in one place."""

    codes: list[str] = field(default_factory=list)
    last_vial_id: int = 0
    last_print_code: str = ""
    last_label_path: str = ""
    genotype_name: str = ""
    printed_count: int = 0
    failed_count: int = 0
    first_print_error: str | None = None


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
            # Ensure a stock-keeper + org-unit exist even on a fresh DB
            # (they're still the fallback owner for shared-scope vials).
            _upsert_default_user(s)
            default_unit = _upsert_default_org_unit(s)
            # Preselect the OS user as the owner — the common flow on a
            # shared tablet is "I am creating a vial for myself". If they
            # want to hand it to someone else the combo is still there.
            preselect_owner_id = current_user_id(s)

            # Dropped-from-stock genotypes don't appear in New Vial — the
            # user has consciously declared those gone and we shouldn't
            # tempt them into recreating a vial by accident.
            genos = s.exec(
                select(Genotype).where(Genotype.is_in_stock.is_(True)).order_by(Genotype.name)
            ).all()
            users = s.exec(select(User).order_by(User.username)).all()
            units = s.exec(select(OrgUnit).order_by(OrgUnit.name)).all()

            default_unit_id = default_unit.id

        for g in genos:
            self.genotype_box.addItem(_genotype_display(g), userData=g.id)

        for u in users:
            label = f"{u.username} — {u.full_name or ''}".strip(" —")
            self.owner_box.addItem(label, userData=u.id)
        idx = self.owner_box.findData(preselect_owner_id)
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
    # Submit — four-step orchestrator
    # ------------------------------------------------------------------

    def _on_ok(self) -> None:
        inputs = self._resolve_inputs()
        if inputs is None:
            return

        do_print = self._gate_printer(inputs.do_print)
        if do_print is None:
            return  # user hit CANCEL at the reconnect dialog

        outcome = self._run_batch(inputs, do_print=do_print)
        if not outcome.codes:
            return  # batch failed before the first vial was persisted

        self._report_print_failures(outcome, inputs.count)
        self.result = self._build_result(inputs, outcome, do_print=do_print)
        self.accept()

    # --- step 1: resolve form values ----------------------------------

    def _resolve_inputs(self) -> _Inputs | None:
        geno_id = self._selected_genotype_id()
        if geno_id is None:
            QMessageBox.warning(
                self,
                "Missing genotype",
                "Pick a genotype from the list, or type its name / donor#id.",
            )
            return None
        owner_id = self.owner_box.currentData()
        if owner_id is None:
            QMessageBox.warning(self, "Missing owner", "Pick an owner for the vial.")
            return None
        return _Inputs(
            genotype_id=geno_id,
            owner_id=owner_id,
            org_unit_id=self.unit_box.currentData(),  # may be None; workflow accepts it
            notes=self.notes_edit.text().strip() or None,
            count=int(self.count_spin.value()),
            do_print=self.print_chk.isChecked(),
        )

    # --- step 2: printer gate -----------------------------------------

    def _gate_printer(self, wants_print: bool) -> bool | None:
        """Return whether the batch should actually print.

        Returns:
            True  — go print.
            False — create vials but don't print (user chose SKIP, or
                    printing wasn't requested in the first place).
            None  — user chose CANCEL; caller should abort the whole
                    batch.
        """
        if not wants_print:
            return False
        choice = check_printer_or_ask(self)
        if choice is ReconnectChoice.CANCEL:
            return None
        return choice is not ReconnectChoice.SKIP

    # --- step 3: batch loop -------------------------------------------

    def _run_batch(self, inputs: _Inputs, *, do_print: bool) -> _BatchOutcome:
        """Create `inputs.count` vials; optionally print each label.

        Prints are best-effort: a failure (errno 16 retries aside) marks
        one vial as un-printed but does not stop the batch — the DB row
        is already persisted and the user can re-print later.
        """
        outcome = _BatchOutcome()
        # Lazy-import printing only when actually needed.
        printer_error_cls: type | None = None
        print_png = None
        if do_print:
            from ddb.printing.service import PrinterError as _PrinterError
            from ddb.printing.service import print_png as _print_png

            print_png = _print_png
            printer_error_cls = _PrinterError

        try:
            for i in range(inputs.count):
                created = self._create_one(inputs, outcome)
                outcome.codes.append(created.vial.print_code)
                outcome.last_vial_id = created.vial.id
                outcome.last_print_code = created.vial.print_code
                outcome.last_label_path = str(created.label_path)

                if do_print and print_png is not None and printer_error_cls is not None:
                    self._try_print(
                        created.label_path.read_bytes(),
                        print_png=print_png,
                        printer_error_cls=printer_error_cls,
                        outcome=outcome,
                    )
                    # Settle between prints (but not after the last one).
                    if i < inputs.count - 1:
                        time.sleep(_BATCH_PRINT_SETTLE_S)
        except WorkflowError as e:
            QMessageBox.critical(
                self,
                "Could not create vial",
                f"{e}\n\nCreated {len(outcome.codes)} of {inputs.count} before the error.",
            )
        return outcome

    def _create_one(self, inputs: _Inputs, outcome: _BatchOutcome):
        """Persist one vial + remember the genotype name for the summary."""
        with Session(engine) as s:
            # The person clicking the button (actor) may differ from the
            # vial's designated owner (e.g. a student prepping vials for
            # a colleague). Actor comes from the OS user; owner still
            # comes off the combobox.
            created = create_vial(
                s,
                genotype_id=inputs.genotype_id,
                actor_id=current_user_id(s),
                owner_id=inputs.owner_id,
                org_unit_id=inputs.org_unit_id,
                notes=inputs.notes,
            )
            if not outcome.genotype_name:
                geno = s.get(Genotype, inputs.genotype_id)
                outcome.genotype_name = geno.name if geno else ""
        return created

    @staticmethod
    def _try_print(
        label_bytes: bytes,
        *,
        print_png,
        printer_error_cls: type,
        outcome: _BatchOutcome,
    ) -> None:
        """Send one label; record success or first-error on the outcome."""
        try:
            print_png(label_bytes)
            outcome.printed_count += 1
        except (printer_error_cls, OSError, ConnectionError) as e:
            outcome.failed_count += 1
            if outcome.first_print_error is None:
                outcome.first_print_error = str(e)

    # --- step 4: report + result --------------------------------------

    def _report_print_failures(self, outcome: _BatchOutcome, total: int) -> None:
        if outcome.failed_count == 0 or outcome.first_print_error is None:
            return
        QMessageBox.warning(
            self,
            "Print partially failed",
            f"{outcome.failed_count} of {total} labels failed to print.\n\n"
            f"First error: {outcome.first_print_error}\n\n"
            "Vials are in the database; you can re-print from the Scan tab's "
            "Detail panel.",
        )

    def _build_result(
        self, inputs: _Inputs, outcome: _BatchOutcome, *, do_print: bool
    ) -> CreateVialResult:
        return CreateVialResult(
            vial_id=outcome.last_vial_id,
            print_code=outcome.last_print_code,
            label_path=outcome.last_label_path,
            genotype_name=outcome.genotype_name,
            printed=outcome.printed_count > 0,
            print_message=(
                None
                if not do_print
                else f"{outcome.printed_count}/{inputs.count} printed"
                + (f" ({outcome.failed_count} failed)" if outcome.failed_count else "")
            ),
            batch_print_codes=outcome.codes,
            printed_count=outcome.printed_count,
            failed_count=outcome.failed_count,
        )


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
