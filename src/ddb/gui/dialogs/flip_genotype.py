"""Flip-all-vials-of-a-genotype dialog.

The Genotypes tab's "Flip all active…" button opens this. Pick an
in-stock genotype (default = the one you flipped last time — the
dark-flies cycle is the archetypal use case, but the same UI works for
any lab whose common workflow is "flip every vial of one strain in one
motion, print one label per child"), confirm, and every currently-active
vial of that genotype is flipped in a single transaction. Each child
gets a fresh label PNG which is then printed sequentially with the same
Bluetooth-settle delay CreateVialDialog uses.

Result surfaces via `FlipGenotypeDialog.result` for callers that want
to show a summary or refresh a tab.
"""

from __future__ import annotations

import contextlib
import time
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy import func
from sqlmodel import Session, select

from ddb.config import settings
from ddb.db import engine
from ddb.gui.dialogs.printer_reconnect import ReconnectChoice, check_printer_or_ask
from ddb.gui.printer_status import shared_monitor
from ddb.models import Genotype, User, Vial
from ddb.workflows import (
    GenotypeNotFoundError,
    WorkflowError,
    flip_active_vials_for_genotype,
)

# Match CreateVialDialog / DetailPanel: the Brother's BT stack needs a
# short pause between raster jobs, or the second one comes back as an
# ECONNRESET while the first socket is still closing.
_BATCH_PRINT_SETTLE_S = 1.0


@dataclass
class FlipGenotypeResult:
    """Surfaced back to the caller so the host tab can show a summary
    line and refresh its list. Empty `child_print_codes` means the
    genotype had no active vials (a benign no-op)."""

    genotype_id: int
    genotype_name: str
    child_print_codes: list[str] = field(default_factory=list)
    printed_count: int = 0
    failed_count: int = 0
    first_print_error: str | None = None


class FlipGenotypeDialog(QDialog):
    """Genotype picker + preview count + confirm — one atomic batch."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Flip all active vials of a genotype")
        self.setMinimumWidth(480)
        self.result: FlipGenotypeResult | None = None

        # In-memory {combo-index → (genotype_id, active_count)} so the
        # preview line and the workflow call don't need a second DB hit.
        self._by_index: list[tuple[int, int]] = []

        intro = QLabel(
            "Every currently-active vial of the chosen genotype will be "
            "flipped in one transaction. Each parent gets one successor "
            "with the same owner and org unit, and one label per child "
            "is printed (Bluetooth batch with a short settle between "
            "jobs). This is the dark-flies workflow — but any strain "
            "you flip as a group works the same way."
        )
        intro.setWordWrap(True)

        self.genotype_box = QComboBox()
        self.count_lbl = QLabel("-")

        form = QFormLayout()
        form.addRow("Genotype:", self.genotype_box)
        form.addRow("Active vials:", self.count_lbl)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        # Rename OK → "Flip all" so the destructive-ish action is
        # named where the user reads it, not implied.
        self.flip_btn = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.flip_btn.setText("Flip all")
        self.buttons.accepted.connect(self._on_ok)
        self.buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addLayout(form)
        layout.addWidget(self.buttons)

        self.genotype_box.currentIndexChanged.connect(self._refresh_count)
        self._populate_genotypes()

    # ------------------------------------------------------------------
    # Genotype list — only in-stock strains, sorted by active-vial count
    # (descending) so the ones with something to flip float to the top.
    # ------------------------------------------------------------------

    def _populate_genotypes(self) -> None:
        with Session(engine) as s:
            counts = dict(
                s.exec(
                    select(Vial.genotype_id, func.count(Vial.id))
                    .where(Vial.is_active.is_(True))
                    .group_by(Vial.genotype_id)
                ).all()
            )
            rows = list(
                s.exec(
                    select(Genotype)
                    .where(Genotype.is_in_stock.is_(True))
                    .order_by(Genotype.name)
                ).all()
            )

        # Sort: strains with active vials first (descending count), then
        # alphabetical for the rest.
        rows.sort(key=lambda g: (-counts.get(g.id, 0), g.name.lower()))

        preselect_index = 0
        for i, g in enumerate(rows):
            n = counts.get(g.id, 0)
            label = f"{g.name}   ·  {n} active"
            self.genotype_box.addItem(label)
            self._by_index.append((g.id, n))
            if g.id == settings.last_flip_all_genotype_id:
                preselect_index = i

        if self._by_index:
            self.genotype_box.setCurrentIndex(preselect_index)
            self._refresh_count(preselect_index)
        else:
            self.count_lbl.setText("<i>No in-stock genotypes.</i>")
            self.count_lbl.setTextFormat(Qt.TextFormat.RichText)
            self.flip_btn.setEnabled(False)

    def _refresh_count(self, idx: int) -> None:
        if not (0 <= idx < len(self._by_index)):
            return
        _, n = self._by_index[idx]
        self.count_lbl.setText(f"<b>{n}</b> vial{'s' if n != 1 else ''}")
        self.count_lbl.setTextFormat(Qt.TextFormat.RichText)
        self.flip_btn.setEnabled(n > 0)

    # ------------------------------------------------------------------
    # Confirm + run
    # ------------------------------------------------------------------

    def _on_ok(self) -> None:
        idx = self.genotype_box.currentIndex()
        if not (0 <= idx < len(self._by_index)):
            return
        genotype_id, n = self._by_index[idx]
        if n <= 0:
            return
        genotype_name = self.genotype_box.currentText().split("   ·")[0]

        confirm = QMessageBox.question(
            self,
            "Flip all vials?",
            f"Flip <b>all {n}</b> active vial{'s' if n != 1 else ''} of "
            f"<b>{genotype_name}</b>?<br><br>"
            "Each parent will be decommissioned and one successor created. "
            "One label per child will be printed if the printer is enabled.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        do_print = settings.printer_enabled and settings.printer_auto_print
        if do_print:
            choice = check_printer_or_ask(self)
            if choice is ReconnectChoice.CANCEL:
                return
            do_print = choice is not ReconnectChoice.SKIP

        self.flip_btn.setEnabled(False)
        try:
            with Session(engine) as s:
                keeper = s.exec(
                    select(User).where(User.username == settings.default_owner_username)
                ).first()
                actor_id = keeper.id if keeper else None
                created = flip_active_vials_for_genotype(
                    s, genotype_id=genotype_id, actor_id=actor_id
                )
                children = [(c.vial.print_code, Path(str(c.label_path))) for c in created]
        except GenotypeNotFoundError as e:
            QMessageBox.critical(self, "Flip failed", str(e))
            self.flip_btn.setEnabled(True)
            return
        except WorkflowError as e:
            QMessageBox.critical(self, "Flip failed", str(e))
            self.flip_btn.setEnabled(True)
            return

        # Persist the choice so next time this dialog opens with the
        # same genotype pre-selected. Same _upsert_env_var pattern
        # settings_tab uses for GUI knobs.
        _persist_last_flip_genotype_id(genotype_id)

        printed_count, failed_count, first_print_error = _batch_print(children, do_print)

        self.result = FlipGenotypeResult(
            genotype_id=genotype_id,
            genotype_name=genotype_name,
            child_print_codes=[c[0] for c in children],
            printed_count=printed_count,
            failed_count=failed_count,
            first_print_error=first_print_error,
        )

        if failed_count and first_print_error is not None:
            QMessageBox.warning(
                self,
                "Print partially failed",
                f"{failed_count} of {len(children)} labels failed to print.\n\n"
                f"First error: {first_print_error}\n\n"
                "Vials are in the database; you can re-print individual "
                "labels from the Scan tab's detail panel.",
            )

        self.accept()


def _batch_print(
    children: list[tuple[str, Path]], do_print: bool
) -> tuple[int, int, str | None]:
    """Print each label PNG sequentially with the Brother-friendly
    settle delay between jobs. Returns (printed, failed, first_error).
    Lifted out of the dialog so the OK path stays readable."""
    if not do_print or not children:
        return 0, 0, None

    from ddb.printing.service import PrinterError, print_png

    monitor = shared_monitor()
    printed = 0
    failed = 0
    first_err: str | None = None
    for i, (_, label_path) in enumerate(children):
        if not label_path.exists():
            failed += 1
            if first_err is None:
                first_err = f"missing label PNG: {label_path}"
            continue
        try:
            print_png(label_path.read_bytes())
            printed += 1
        except (PrinterError, OSError, ConnectionError) as e:
            failed += 1
            if first_err is None:
                first_err = str(e)
            if monitor is not None:
                monitor.force_probe()
        if i < len(children) - 1:
            time.sleep(_BATCH_PRINT_SETTLE_S)
    return printed, failed, first_err


def _persist_last_flip_genotype_id(genotype_id: int) -> None:
    """Update the in-memory setting and mirror it into `.env` so the
    preselection survives restart. Kept private to this dialog so the
    write path is one line."""
    settings.last_flip_all_genotype_id = genotype_id
    # Import inside the function so the CLI doesn't pull in the GUI's
    # env-file helper (settings_tab is a GUI-only module).
    from ddb.gui.settings_tab import _env_path, _upsert_env_var

    # Not fatal if the .env write fails — the in-memory update above
    # already picked up the change for this session; the user just
    # won't get preselection on the next launch.
    with contextlib.suppress(OSError):
        _upsert_env_var(_env_path(), "DDB_LAST_FLIP_ALL_GENOTYPE_ID", str(genotype_id))
