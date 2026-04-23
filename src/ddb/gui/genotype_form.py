"""Reusable genotype field-set widget.

Used by:
  - Genotypes tab inline editor (edit-in-place on the right panel)
  - Create-Genotype dialog (blank form for a new entry)

Having one form for both paths means the inline-edit bug can't disagree
with the create-dialog bug — they're the same fields, same validation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from PySide6.QtWidgets import QCheckBox, QComboBox, QFormLayout, QLineEdit, QWidget
from sqlmodel import Session, select

from ddb.db import engine
from ddb.models import Donor, Genotype

NEW_DONOR_SENTINEL = "__new__"


@dataclass
class GenotypeFormValues:
    name: str
    donor_strain_id: str | None
    is_wildtype: bool
    chromosome_x: str | None
    chromosome_2: str | None
    chromosome_3: str | None
    chromosome_4: str | None
    chromosome_y: str | None
    phenotype: str | None
    notes: str | None
    # Either an existing donor id, or None (drop the donor link), or a
    # brand-new donor name when the "+ New donor…" choice was used.
    donor_id: int | None
    new_donor_name: str | None

    def chromosome_fields(self) -> dict[str, Any]:
        return {
            "chromosome_x": self.chromosome_x,
            "chromosome_2": self.chromosome_2,
            "chromosome_3": self.chromosome_3,
            "chromosome_4": self.chromosome_4,
            "chromosome_y": self.chromosome_y,
        }


class GenotypeForm(QWidget):
    """All the fields needed to create or edit a Genotype (except audit meta)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self.name_edit = QLineEdit()
        self.donor_strain_edit = QLineEdit()
        self.wildtype_chk = QCheckBox("Wildtype (confirmed)")
        self.chrom_x_edit = QLineEdit()
        self.chrom_2_edit = QLineEdit()
        self.chrom_3_edit = QLineEdit()
        self.chrom_4_edit = QLineEdit()
        self.chrom_y_edit = QLineEdit()
        self.phenotype_edit = QLineEdit()
        self.notes_edit = QLineEdit()

        self.donor_box = QComboBox()
        self.new_donor_edit = QLineEdit()
        self.new_donor_edit.setPlaceholderText("New donor name…")
        self.new_donor_edit.setVisible(False)
        self.donor_box.currentIndexChanged.connect(self._toggle_new_donor_field)

        form = QFormLayout(self)
        form.addRow("Name:", self.name_edit)
        form.addRow("Donor:", self.donor_box)
        form.addRow("", self.new_donor_edit)
        form.addRow("Donor strain id:", self.donor_strain_edit)
        form.addRow("", self.wildtype_chk)
        form.addRow("Chromosome X:", self.chrom_x_edit)
        form.addRow("Chromosome 2:", self.chrom_2_edit)
        form.addRow("Chromosome 3:", self.chrom_3_edit)
        form.addRow("Chromosome 4:", self.chrom_4_edit)
        form.addRow("Chromosome Y:", self.chrom_y_edit)
        form.addRow("Phenotype:", self.phenotype_edit)
        form.addRow("Notes:", self.notes_edit)

        self._reload_donors()

    # ------------------------------------------------------------------
    # Donor dropdown
    # ------------------------------------------------------------------

    def _reload_donors(self) -> None:
        self.donor_box.blockSignals(True)
        try:
            self.donor_box.clear()
            self.donor_box.addItem("(none)", userData=None)
            with Session(engine) as s:
                donors = s.exec(select(Donor).order_by(Donor.name)).all()
            for d in donors:
                self.donor_box.addItem(d.name, userData=d.id)
            self.donor_box.addItem("+ New donor…", userData=NEW_DONOR_SENTINEL)
        finally:
            self.donor_box.blockSignals(False)

    def _toggle_new_donor_field(self) -> None:
        data = self.donor_box.currentData()
        self.new_donor_edit.setVisible(data == NEW_DONOR_SENTINEL)
        if data == NEW_DONOR_SENTINEL:
            self.new_donor_edit.setFocus()
        else:
            self.new_donor_edit.clear()

    # ------------------------------------------------------------------
    # Populate / collect
    # ------------------------------------------------------------------

    def populate_from(self, g: Genotype) -> None:
        """Fill the form from an existing Genotype for editing."""
        self._reload_donors()
        self.name_edit.setText(g.name)
        self.donor_strain_edit.setText(g.donor_strain_id or "")
        self.wildtype_chk.setChecked(bool(g.is_wildtype))
        self.chrom_x_edit.setText(g.chromosome_x or "")
        self.chrom_2_edit.setText(g.chromosome_2 or "")
        self.chrom_3_edit.setText(g.chromosome_3 or "")
        self.chrom_4_edit.setText(g.chromosome_4 or "")
        self.chrom_y_edit.setText(g.chromosome_y or "")
        self.phenotype_edit.setText(g.phenotype or "")
        self.notes_edit.setText(g.notes or "")
        # Select the matching donor in the combo.
        idx = self.donor_box.findData(g.donor_id) if g.donor_id else self.donor_box.findData(None)
        if idx >= 0:
            self.donor_box.setCurrentIndex(idx)

    def clear(self) -> None:
        self._reload_donors()
        for edit in (
            self.name_edit,
            self.donor_strain_edit,
            self.chrom_x_edit,
            self.chrom_2_edit,
            self.chrom_3_edit,
            self.chrom_4_edit,
            self.chrom_y_edit,
            self.phenotype_edit,
            self.notes_edit,
            self.new_donor_edit,
        ):
            edit.clear()
        self.wildtype_chk.setChecked(False)
        self.donor_box.setCurrentIndex(0)  # "(none)"

    def values(self) -> GenotypeFormValues:
        donor_data = self.donor_box.currentData()
        if donor_data == NEW_DONOR_SENTINEL:
            donor_id = None
            new_donor_name = self.new_donor_edit.text().strip() or None
        else:
            donor_id = donor_data
            new_donor_name = None

        def _opt(edit: QLineEdit) -> str | None:
            return edit.text().strip() or None

        return GenotypeFormValues(
            name=self.name_edit.text().strip(),
            donor_strain_id=_opt(self.donor_strain_edit),
            is_wildtype=self.wildtype_chk.isChecked(),
            chromosome_x=_opt(self.chrom_x_edit),
            chromosome_2=_opt(self.chrom_2_edit),
            chromosome_3=_opt(self.chrom_3_edit),
            chromosome_4=_opt(self.chrom_4_edit),
            chromosome_y=_opt(self.chrom_y_edit),
            phenotype=_opt(self.phenotype_edit),
            notes=_opt(self.notes_edit),
            donor_id=donor_id,
            new_donor_name=new_donor_name,
        )


def ensure_donor(session: Session, values: GenotypeFormValues) -> int | None:
    """If the form asked for a new donor, create it and return its id.

    Otherwise return whatever `values.donor_id` was (possibly None).
    Commits so the id is durable before it's referenced from a genotype row.
    """
    if values.new_donor_name:
        donor = session.exec(select(Donor).where(Donor.name == values.new_donor_name)).first()
        if donor is None:
            donor = Donor(name=values.new_donor_name)
            session.add(donor)
            session.commit()
            session.refresh(donor)
        return donor.id
    return values.donor_id
