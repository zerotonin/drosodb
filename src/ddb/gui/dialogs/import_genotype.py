"""Import a Genotype by stock-center ID, backed by the local FlyBase catalog.

Flow:
  1. User picks a source (Bloomington / Vienna / Kyoto / … or "FlyBase FBst")
     from a dropdown populated from whatever collections are actually in
     the on-disk catalog, then types a stock ID and clicks Lookup.
  2. On a hit, the dialog reveals a full GenotypeForm prefilled with the
     parsed record — name, donor_strain_id, chromosomes split positionally,
     donor combo already pointing at the canonical collection Donor.
  3. User edits anything they want, clicks Create.
  4. If a Genotype with the same `donor_strain_id` already exists, a 3-way
     prompt (Open existing / Create duplicate with note / Abort) decides
     what happens — reordered stocks are a legitimate reason to keep both
     rows, so duplicate-with-note is preserved deliberately.

The dialog never mutates the DB until Create. `created_genotype_id` and
`open_existing_genotype_id` expose the outcome to the caller so the
Genotypes tab can select the right row after the dialog closes.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from sqlmodel import Session, select

from ddb.config import settings
from ddb.flybase import (
    available_collections,
    find_by_fbst,
    find_record,
    parse_genotype_string,
    paths_for,
    read_meta,
)
from ddb.flybase.donors import ensure_collection_donor
from ddb.flybase.lookup import CatalogRecord
from ddb.gui.genotype_form import GenotypeForm, ensure_donor
from ddb.models import Genotype

_FBST_LABEL = "FlyBase ID (FBst…)"


class ImportGenotypeDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Import genotype")
        self.setModal(True)
        self.setMinimumWidth(640)

        # Set by _on_create — callers read these to decide what to do
        # after .exec() returns.
        self.created_genotype_id: int | None = None
        self.open_existing_genotype_id: int | None = None

        # --- Source selector row ---
        self.source_box = QComboBox()
        self.id_edit = QLineEdit()
        self.id_edit.setPlaceholderText("e.g. 9405")
        self.lookup_btn = QPushButton("Lookup")
        self.lookup_btn.clicked.connect(self._on_lookup)
        self.id_edit.returnPressed.connect(self._on_lookup)

        top_row = QHBoxLayout()
        top_row.addWidget(QLabel("Source:"))
        top_row.addWidget(self.source_box, stretch=1)
        top_row.addWidget(QLabel("ID:"))
        top_row.addWidget(self.id_edit)
        top_row.addWidget(self.lookup_btn)

        # --- Result / preview zone ---
        self.result_lbl = QLabel("<i>Pick a source, type an ID, and click Lookup.</i>")
        self.result_lbl.setTextFormat(Qt.TextFormat.RichText)
        self.result_lbl.setWordWrap(True)

        hr = QFrame()
        hr.setFrameShape(QFrame.Shape.HLine)
        hr.setFrameShadow(QFrame.Shadow.Sunken)

        # The full editor form — hidden until a lookup succeeds so the
        # dialog doesn't shout at the user with empty fields before
        # there's anything to prefill.
        self.form = GenotypeForm(self)
        self.form.setVisible(False)

        # --- Bottom buttons ---
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.create_btn = self.buttons.addButton(
            "Create genotype", QDialogButtonBox.ButtonRole.AcceptRole
        )
        self.create_btn.setEnabled(False)
        self.create_btn.clicked.connect(self._on_create)
        self.buttons.rejected.connect(self.reject)

        lo = QVBoxLayout(self)
        lo.addLayout(top_row)
        lo.addWidget(self.result_lbl)
        lo.addWidget(hr)
        lo.addWidget(self.form)
        lo.addStretch()
        lo.addWidget(self.buttons)

        # Track the raw catalog record behind the currently-previewed
        # lookup — used by duplicate detection + auto-generated notes.
        self._current_record: CatalogRecord | None = None

        self._populate_sources()

    # ------------------------------------------------------------------
    # Source dropdown — driven by the on-disk catalog meta
    # ------------------------------------------------------------------

    def _catalog_path(self) -> Path:
        return paths_for(settings.data_dir).tsv_gz

    def _populate_sources(self) -> None:
        """Read the local catalog meta for the collection list. If the
        catalog isn't downloaded yet, disable everything and explain."""
        paths = paths_for(settings.data_dir)
        meta = read_meta(paths)
        if meta is None or not paths.tsv_gz.is_file():
            self.source_box.addItem("(catalog not downloaded)")
            self.source_box.setEnabled(False)
            self.id_edit.setEnabled(False)
            self.lookup_btn.setEnabled(False)
            self.result_lbl.setText(
                "<b>FlyBase catalog isn't downloaded yet.</b><br>"
                "Open <i>Settings → Genotype catalog → Download now</i>, "
                "then come back."
            )
            return
        # `available_collections` returns ordered (by count desc) from the
        # live TSV; same source the Settings help dialog uses.
        for name, count in available_collections(paths.tsv_gz).items():
            self.source_box.addItem(f"{name}   ({count:,})", userData=name)
        self.source_box.addItem(_FBST_LABEL, userData="__fbst__")

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def _on_lookup(self) -> None:
        if not self.source_box.isEnabled():
            return
        source = self.source_box.currentData()
        query = self.id_edit.text().strip()
        if not query:
            self.result_lbl.setText("<i>Type a stock ID first.</i>")
            return

        tsv_path = self._catalog_path()
        if source == "__fbst__":
            # Accept both `FBst0009405` and `9405` (we'll zero-pad).
            fbst = query if query.lower().startswith("fbst") else f"FBst{int(query):07d}"
            record = find_by_fbst(tsv_path, fbst)
        else:
            record = find_record(tsv_path, source, query)

        if record is None:
            self._current_record = None
            self.form.setVisible(False)
            self.create_btn.setEnabled(False)
            self.result_lbl.setText(
                f"<b>No match.</b> <code>{source}</code> doesn't have a "
                f"stock ID <code>{query}</code> in the current catalog."
            )
            return

        self._apply_record(record)

    def _apply_record(self, rec: CatalogRecord) -> None:
        """Populate the preview line and the underlying form from a hit."""
        self._current_record = rec

        living = "yes" if rec.is_living_stock else "<b style='color:#a00'>no (lost)</b>"
        self.result_lbl.setText(
            f"<b>{rec.collection} {rec.stock_number}</b> "
            f"(<code>{rec.fbst}</code>, {rec.species})<br>"
            f"<i>{rec.genotype}</i><br>"
            f"Living stock: {living}"
        )

        # Make sure the canonical donor row exists BEFORE we reload the
        # form's donor combo, so "select it" below finds it.
        from ddb.db import engine

        with Session(engine) as s:
            donor = ensure_collection_donor(s, rec.collection)
            donor_id = donor.id

        parsed = parse_genotype_string(rec.genotype)
        # Default name = stock number. User can rename.
        self.form.clear()
        self.form.name_edit.setText(rec.stock_number)
        self.form.donor_strain_edit.setText(rec.stock_number)
        self.form.chrom_x_edit.setText(parsed.chromosome_x)
        self.form.chrom_2_edit.setText(parsed.chromosome_2)
        self.form.chrom_3_edit.setText(parsed.chromosome_3)
        self.form.chrom_4_edit.setText(parsed.chromosome_4)
        self.form.chrom_y_edit.setText(parsed.chromosome_y)
        # Select the canonical donor in the combo.
        idx = self.form.donor_box.findData(donor_id)
        if idx >= 0:
            self.form.donor_box.setCurrentIndex(idx)

        self.form.setVisible(True)
        self.create_btn.setEnabled(True)

    # ------------------------------------------------------------------
    # Create (with duplicate handling)
    # ------------------------------------------------------------------

    def _on_create(self) -> None:
        values = self.form.values()
        if not values.name:
            QMessageBox.warning(self, "Missing name", "Genotype name cannot be empty.")
            return

        from ddb.db import engine

        # Duplicate detection keys on donor_strain_id — the BDSC/Kyoto/VDRC
        # number is the stable identity from the user's POV.
        with Session(engine) as s:
            existing = None
            if values.donor_strain_id:
                existing = s.exec(
                    select(Genotype).where(
                        Genotype.donor_strain_id == values.donor_strain_id
                    )
                ).first()

        if existing is not None:
            choice = _prompt_duplicate(self, existing)
            if choice == "abort":
                return
            if choice == "open":
                self.open_existing_genotype_id = existing.id
                self.accept()
                return
            if choice == "duplicate":
                # Append the duplicate-reason note to whatever the user
                # already typed. Keep it short but unambiguous.
                stamp = date.today().isoformat()
                marker = (
                    f"Re-ordered {stamp}; retained separately from "
                    f"id={existing.id} ({existing.name}) because the existing "
                    f"lab stock may be contaminated."
                )
                values.notes = (
                    f"{values.notes}\n{marker}" if values.notes else marker
                )

        try:
            with Session(engine) as s:
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

        self.created_genotype_id = g.id
        self.accept()


def _prompt_duplicate(parent: QWidget, existing: Genotype) -> str:
    """Three-way prompt used when a lookup hits an existing Genotype.

    Returns "open" / "duplicate" / "abort". Split out as a module-level
    helper so the core dialog stays focused; same reason the buttons are
    built by hand rather than via QMessageBox.question (we want three
    custom captions, not Yes/No/Cancel).
    """
    msg = QMessageBox(parent)
    msg.setIcon(QMessageBox.Icon.Question)
    msg.setWindowTitle("Duplicate stock ID")
    msg.setText(
        f"A genotype with donor_strain_id <b>{existing.donor_strain_id}</b> "
        f"already exists in the database as <b>{existing.name}</b> "
        f"(id={existing.id}).<br><br>"
        "What do you want to do?"
    )
    msg.setInformativeText(
        "<b>Open existing</b> jumps to it for editing.<br>"
        "<b>Create duplicate</b> keeps both rows and tags the new one with a "
        "re-order note (use this when you re-ordered because the lab stock "
        "may be contaminated).<br>"
        "<b>Abort</b> cancels this import."
    )
    open_btn = msg.addButton("Open existing", QMessageBox.ButtonRole.AcceptRole)
    dup_btn = msg.addButton("Create duplicate", QMessageBox.ButtonRole.DestructiveRole)
    msg.addButton("Abort", QMessageBox.ButtonRole.RejectRole)
    msg.setDefaultButton(open_btn)
    msg.exec()
    clicked = msg.clickedButton()
    if clicked is open_btn:
        return "open"
    if clicked is dup_btn:
        return "duplicate"
    return "abort"
