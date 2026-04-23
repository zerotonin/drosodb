"""Genotypes tab — list on the left, editable detail on the right.

- Reload: refetch the list.
- New: open the CreateGenotype dialog (same field set as inline edit).
- Inline edit: select a row, edit fields in the right-hand form, Save.

The inline form and the create dialog share `GenotypeForm`, so rename /
donor-fix / chromosome-typo fixes and brand-new entries go through the
same validation.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from sqlmodel import Session, select

from ddb.config import settings
from ddb.db import engine
from ddb.genotype import format_notation
from ddb.gui.dialogs import CreateGenotypeDialog
from ddb.gui.genotype_form import GenotypeForm, ensure_donor
from ddb.models import Genotype, User
from ddb.workflows import WorkflowError, update_genotype


class GenotypesTab(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._current_id: int | None = None
        self._suppress_dirty = False

        # --- Left: list + New / Reload --------------------------------------
        self.list = QListWidget()
        self.list.setMinimumWidth(240)
        self.list.currentItemChanged.connect(self._on_selection_changed)

        self.new_btn = QPushButton("New")
        self.refresh_btn = QPushButton("Reload")
        self.new_btn.clicked.connect(self._open_new_dialog)
        self.refresh_btn.clicked.connect(self.reload)

        left_btns = QHBoxLayout()
        left_btns.addWidget(self.new_btn)
        left_btns.addWidget(self.refresh_btn)
        left_btns.addStretch()

        left = QVBoxLayout()
        left.addWidget(self.list, stretch=1)
        left.addLayout(left_btns)
        left_w = QWidget()
        left_w.setLayout(left)

        # --- Right: editable detail form using the shared GenotypeForm -----
        self.form = GenotypeForm(self)
        # Dirty-marking: any text change or checkbox toggle enables Save.
        self.form.name_edit.textChanged.connect(self._mark_dirty)
        self.form.donor_strain_edit.textChanged.connect(self._mark_dirty)
        self.form.chrom_x_edit.textChanged.connect(self._mark_dirty)
        self.form.chrom_2_edit.textChanged.connect(self._mark_dirty)
        self.form.chrom_3_edit.textChanged.connect(self._mark_dirty)
        self.form.chrom_4_edit.textChanged.connect(self._mark_dirty)
        self.form.chrom_y_edit.textChanged.connect(self._mark_dirty)
        self.form.phenotype_edit.textChanged.connect(self._mark_dirty)
        self.form.notes_edit.textChanged.connect(self._mark_dirty)
        self.form.new_donor_edit.textChanged.connect(self._mark_dirty)
        self.form.donor_box.currentIndexChanged.connect(lambda _i: self._mark_dirty(""))
        self.form.wildtype_chk.toggled.connect(lambda _b: self._mark_dirty(""))

        self.meta_lbl = QLabel("<i>Select a genotype on the left or click New.</i>")
        self.meta_lbl.setTextFormat(Qt.TextFormat.RichText)
        self.meta_lbl.setWordWrap(True)

        self.save_btn = QPushButton("Save")
        self.revert_btn = QPushButton("Revert")
        self.save_btn.setEnabled(False)
        self.revert_btn.setEnabled(False)
        self.save_btn.clicked.connect(self._save)
        self.revert_btn.clicked.connect(self._load_current)
        btns = QHBoxLayout()
        btns.addWidget(self.save_btn)
        btns.addWidget(self.revert_btn)
        btns.addStretch()

        right = QVBoxLayout()
        right.addWidget(self.form)
        right.addWidget(self.meta_lbl)
        right.addStretch()
        right.addLayout(btns)
        right_w = QWidget()
        right_w.setLayout(right)

        outer = QHBoxLayout(self)
        outer.addWidget(left_w, stretch=1)
        outer.addWidget(right_w, stretch=2)

        self.reload()

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------

    def reload(self) -> None:
        keep_id = self._current_id
        self.list.clear()
        with Session(engine) as s:
            for g in s.exec(select(Genotype).order_by(Genotype.id)).all():
                label = f"{g.id:>3}  {g.name}"
                if g.donor_strain_id:
                    label += f"  (#{g.donor_strain_id})"
                item = QListWidgetItem(label)
                item.setData(Qt.ItemDataRole.UserRole, g.id)
                self.list.addItem(item)
        if keep_id is not None:
            for i in range(self.list.count()):
                if self.list.item(i).data(Qt.ItemDataRole.UserRole) == keep_id:
                    self.list.setCurrentRow(i)
                    return
        if self.list.count() > 0:
            self.list.setCurrentRow(0)

    def _on_selection_changed(self, current: QListWidgetItem | None, _prev) -> None:
        if current is None:
            self._current_id = None
            self._clear_form()
            return
        self._current_id = current.data(Qt.ItemDataRole.UserRole)
        self._load_current()

    def _load_current(self) -> None:
        if self._current_id is None:
            return
        with Session(engine) as s:
            g = s.get(Genotype, self._current_id)
            if g is None:
                return

        self._suppress_dirty = True
        try:
            self.form.populate_from(g)
        finally:
            self._suppress_dirty = False

        self.meta_lbl.setText(
            f"<b>Notation preview:</b> <code>{format_notation(g)}</code> "
            f"&nbsp;·&nbsp; <b>id:</b> {g.id}"
        )
        self._set_dirty(False)

    def _clear_form(self) -> None:
        self._suppress_dirty = True
        try:
            self.form.clear()
        finally:
            self._suppress_dirty = False
        self.meta_lbl.setText("<i>Select a genotype on the left or click New.</i>")
        self._set_dirty(False)

    # ------------------------------------------------------------------
    # Dirty-state + save
    # ------------------------------------------------------------------

    def _mark_dirty(self, _text: str) -> None:
        if self._suppress_dirty or self._current_id is None:
            return
        self._set_dirty(True)

    def _set_dirty(self, dirty: bool) -> None:
        self.save_btn.setEnabled(dirty)
        self.revert_btn.setEnabled(dirty)

    def _save(self) -> None:
        if self._current_id is None:
            return
        values = self.form.values()
        if not values.name:
            QMessageBox.warning(self, "Missing name", "Genotype name cannot be empty.")
            return

        with Session(engine) as s:
            keeper = s.exec(
                select(User).where(User.username == settings.default_owner_username)
            ).first()
            actor_id = keeper.id if keeper else None

            donor_id = ensure_donor(s, values)

            try:
                update_genotype(
                    s,
                    genotype_id=self._current_id,
                    actor_id=actor_id,
                    name=values.name,
                    donor_strain_id=values.donor_strain_id,
                    is_wildtype=values.is_wildtype,
                    donor_id=donor_id,
                    **values.chromosome_fields(),
                    phenotype=values.phenotype,
                    notes=values.notes,
                )
            except WorkflowError as e:
                QMessageBox.critical(self, "Update failed", str(e))
                return

        self.reload()

    # ------------------------------------------------------------------
    # New-genotype dialog
    # ------------------------------------------------------------------

    def _open_new_dialog(self) -> None:
        dlg = CreateGenotypeDialog(self)
        if dlg.exec() != dlg.DialogCode.Accepted or dlg.result is None:
            return
        # Select the newly-created row after reload.
        self._current_id = dlg.result.genotype_id
        self.reload()
