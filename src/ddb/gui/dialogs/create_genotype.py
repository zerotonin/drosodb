"""Create-Genotype dialog — the "New" button in the Genotypes tab opens this.

Same `GenotypeForm` the tab's inline editor uses, so the field set stays
in sync between "edit existing" and "add new".
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QMessageBox, QVBoxLayout
from sqlmodel import Session, select

from ddb.db import engine
from ddb.gui.genotype_form import GenotypeForm, ensure_donor
from ddb.models import Genotype


@dataclass
class CreateGenotypeResult:
    genotype_id: int
    name: str


class CreateGenotypeDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("New genotype")
        self.setModal(True)
        self.result: CreateGenotypeResult | None = None

        self.form = GenotypeForm(self)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self._on_ok)
        self.buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(self.form)
        layout.addWidget(self.buttons)

    def _on_ok(self) -> None:
        values = self.form.values()
        if not values.name:
            QMessageBox.warning(self, "Missing name", "Genotype name cannot be empty.")
            return
        try:
            with Session(engine) as s:
                dupe = s.exec(select(Genotype).where(Genotype.name == values.name)).first()
                if dupe is not None:
                    QMessageBox.warning(
                        self,
                        "Duplicate name",
                        f"A genotype named {values.name!r} already exists "
                        f"(id={dupe.id}). Edit that one instead.",
                    )
                    return
                donor_id = ensure_donor(s, values)
                g = Genotype(
                    name=values.name,
                    chromosome_x=values.chromosome_x,
                    chromosome_2=values.chromosome_2,
                    chromosome_3=values.chromosome_3,
                    chromosome_4=values.chromosome_4,
                    chromosome_y=values.chromosome_y,
                    phenotype=values.phenotype,
                    notes=values.notes,
                    is_wildtype=values.is_wildtype,
                    donor_strain_id=values.donor_strain_id,
                    donor_id=donor_id,
                )
                s.add(g)
                s.commit()
                s.refresh(g)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Create failed", str(e))
            return

        self.result = CreateGenotypeResult(genotype_id=g.id, name=g.name)
        self.accept()
