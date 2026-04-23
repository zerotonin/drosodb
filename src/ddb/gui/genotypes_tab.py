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
from PySide6.QtGui import QColor, QFont
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
from sqlalchemy import func
from sqlmodel import Session, select

from ddb.config import settings
from ddb.db import engine
from ddb.genotype import format_notation
from ddb.gui.dialogs import CreateGenotypeDialog
from ddb.gui.genotype_form import GenotypeForm, ensure_donor
from ddb.models import Genotype, User, Vial
from ddb.workflows import (
    GenotypeStillHasActiveVialsError,
    WorkflowError,
    drop_genotype_from_stock,
    reactivate_genotype_in_stock,
    update_genotype,
)


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
        self.drop_btn = QPushButton("Drop from stock")
        self.drop_btn.setToolTip(
            "Mark this genotype as no longer in stock. The row stays for "
            "lineage/history but is hidden from the New-Vial dropdown."
        )
        self.reactivate_btn = QPushButton("Re-activate")
        self.reactivate_btn.setToolTip(
            "Undo a previous drop — put this genotype back on the "
            "New-Vial dropdown so you can create vials for it again."
        )
        self.save_btn.setEnabled(False)
        self.revert_btn.setEnabled(False)
        self.drop_btn.setEnabled(False)
        self.reactivate_btn.setEnabled(False)
        self.reactivate_btn.setVisible(False)
        self.save_btn.clicked.connect(self._save)
        self.revert_btn.clicked.connect(self._load_current)
        self.drop_btn.clicked.connect(self._drop_from_stock)
        self.reactivate_btn.clicked.connect(self._reactivate_in_stock)
        btns = QHBoxLayout()
        btns.addWidget(self.save_btn)
        btns.addWidget(self.revert_btn)
        btns.addStretch()
        btns.addWidget(self.drop_btn)
        btns.addWidget(self.reactivate_btn)

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
        italic = QFont()
        italic.setItalic(True)
        grey = QColor("#888")
        with Session(engine) as s:
            # Single aggregated query for active-vial counts — O(rows)
            # instead of an N+1 per-genotype lookup.
            active_counts = dict(
                s.exec(
                    select(Vial.genotype_id, func.count(Vial.id))
                    .where(Vial.is_active.is_(True))
                    .group_by(Vial.genotype_id)
                ).all()
            )
            for g in s.exec(select(Genotype).order_by(Genotype.id)).all():
                n = active_counts.get(g.id, 0)
                label = f"{g.id:>3}  {g.name}"
                if g.donor_strain_id:
                    label += f"  (#{g.donor_strain_id})"
                label += f"   ·  {n} vial{'s' if n != 1 else ''}"
                if not g.is_in_stock:
                    label += "   — dropped"
                item = QListWidgetItem(label)
                item.setData(Qt.ItemDataRole.UserRole, g.id)
                if not g.is_in_stock:
                    # Grey italic = "in the DB for history, but not available".
                    item.setForeground(grey)
                    item.setFont(italic)
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

        status = "<b style='color:#a00'>NOT IN STOCK</b>" if not g.is_in_stock else "in stock"
        self.meta_lbl.setText(
            f"<b>Notation preview:</b> <code>{format_notation(g)}</code> "
            f"&nbsp;·&nbsp; <b>id:</b> {g.id} &nbsp;·&nbsp; {status}"
        )
        # Drop is for in-stock strains only; Re-activate shows in its
        # place once the strain is marked not-in-stock. One is always
        # visible, never both — the transition is the whole workflow.
        in_stock = bool(g.is_in_stock)
        self.drop_btn.setVisible(in_stock)
        self.drop_btn.setEnabled(in_stock)
        self.reactivate_btn.setVisible(not in_stock)
        self.reactivate_btn.setEnabled(not in_stock)
        self._set_dirty(False)

    def _clear_form(self) -> None:
        self._suppress_dirty = True
        try:
            self.form.clear()
        finally:
            self._suppress_dirty = False
        self.meta_lbl.setText("<i>Select a genotype on the left or click New.</i>")
        self.drop_btn.setEnabled(False)
        self.reactivate_btn.setEnabled(False)
        self.reactivate_btn.setVisible(False)
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
    # Drop from stock
    # ------------------------------------------------------------------

    def _drop_from_stock(self) -> None:
        if self._current_id is None:
            return
        with Session(engine) as s:
            g = s.get(Genotype, self._current_id)
            if g is None:
                return
            name = g.name
            keeper = s.exec(
                select(User).where(User.username == settings.default_owner_username)
            ).first()
            actor_id = keeper.id if keeper else None

            # Confirm first — this is a user-visible state change.
            ok = QMessageBox.question(
                self,
                "Drop from stock?",
                f"Mark <b>{name}</b> as no longer in stock?<br><br>"
                "The row stays for lineage + history, but it will be hidden "
                "from the New-Vial dropdown.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if ok != QMessageBox.StandardButton.Yes:
                return

            try:
                drop_genotype_from_stock(s, genotype_id=self._current_id, actor_id=actor_id)
            except GenotypeStillHasActiveVialsError as e:
                QMessageBox.critical(
                    self,
                    "Cannot drop — active vials remain",
                    f"<b>{name}</b> still has "
                    f"{len(e.active_print_codes)} active vial(s):<br><br>"
                    f"<code>{', '.join(e.active_print_codes)}</code><br><br>"
                    "Decommission or flip them first, then try again.",
                )
                return
            except WorkflowError as e:
                QMessageBox.critical(self, "Could not drop", str(e))
                return

        QMessageBox.warning(
            self,
            "Genotype dropped",
            f"<b>{name}</b> is now listed as <b>not available</b>. "
            "It has been removed from the New-Vial dropdown.",
        )
        self.reload()

    # ------------------------------------------------------------------
    # Re-activate into stock
    # ------------------------------------------------------------------

    def _reactivate_in_stock(self) -> None:
        if self._current_id is None:
            return
        with Session(engine) as s:
            g = s.get(Genotype, self._current_id)
            if g is None:
                return
            name = g.name
            keeper = s.exec(
                select(User).where(User.username == settings.default_owner_username)
            ).first()
            actor_id = keeper.id if keeper else None

            ok = QMessageBox.question(
                self,
                "Re-activate genotype?",
                f"Put <b>{name}</b> back on the New-Vial dropdown?<br><br>"
                "Any existing audit history is preserved.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if ok != QMessageBox.StandardButton.Yes:
                return

            try:
                reactivate_genotype_in_stock(
                    s, genotype_id=self._current_id, actor_id=actor_id
                )
            except WorkflowError as e:
                QMessageBox.critical(self, "Could not re-activate", str(e))
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
