"""Right-hand vial-detail widget used by the Scan tab.

Kept separate from ScanTab so the scanning-control surface (camera,
buttons, manual lookup) stays readable and the detail panel can be
reused elsewhere in the future. Every field updates via `show_detail`;
the widget owns the Print / Flip / Decommission / Export-lineage
buttons for whichever vial is currently loaded.
"""

from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import (
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from sqlmodel import Session

from ddb.config import settings
from ddb.current_user import current_user_id
from ddb.db import engine
from ddb.gui.dialogs import ReconnectChoice, check_printer_or_ask
from ddb.gui.printer_status import shared_monitor
from ddb.lineage import export_lineage_csv
from ddb.models import Vial
from ddb.reports import VialDetail, vial_detail
from ddb.workflows import (
    VialNotFoundError,
    WorkflowError,
    active_flip_descendant_codes,
    decommission_vial,
    flip_vial,
    multiply_vial,
    reactivate_vial,
)

MULTIPLY_MIN = 2
MULTIPLY_MAX = 12
MULTIPLY_DEFAULT = 4

# Pause between batch prints so the Brother's BT stack can fully close
# the prior RFCOMM socket before we open the next — matches the value
# CreateVialDialog uses for its New-Vial batch loop.
_BATCH_PRINT_SETTLE_S = 1.0


class DetailPanel(QWidget):
    """The right-hand panel. All fields update when a new vial is loaded."""

    # Emitted after a Flip/Decommission so the host tab can refresh its
    # status line. Carries a short human-readable message.
    vial_changed = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self._current_vial_id: int | None = None
        self._current_print_code: str | None = None

        self.header = QLabel("<i>No vial scanned yet.</i>")
        self.header.setTextFormat(Qt.TextFormat.RichText)

        form = QFormLayout()
        self.print_code_lbl = QLabel("-")
        self.status_lbl = QLabel("-")
        self.generation_lbl = QLabel("-")
        self.genotype_lbl = QLabel("-")
        self.notation_lbl = QLabel("-")
        self.notation_lbl.setWordWrap(True)
        self.notation_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.phenotype_lbl = QLabel("-")
        self.owner_lbl = QLabel("-")
        self.unit_lbl = QLabel("-")
        self.donor_lbl = QLabel("-")
        self.created_lbl = QLabel("-")
        self.lineage_lbl = QLabel("-")
        form.addRow("Print code:", self.print_code_lbl)
        form.addRow("Status:", self.status_lbl)
        form.addRow("Generation:", self.generation_lbl)
        form.addRow("Genotype:", self.genotype_lbl)
        form.addRow("Notation:", self.notation_lbl)
        form.addRow("Phenotype:", self.phenotype_lbl)
        form.addRow("Owner:", self.owner_lbl)
        form.addRow("Org unit:", self.unit_lbl)
        form.addRow("Donor:", self.donor_lbl)
        form.addRow("Created:", self.created_lbl)
        form.addRow("Lineage:", self.lineage_lbl)
        form_box = QGroupBox("Vial")
        form_box.setLayout(form)

        self.audit = QTextEdit(readOnly=True)
        self.audit.setMaximumHeight(160)
        audit_box = QGroupBox("Audit trail")
        audit_l = QVBoxLayout()
        audit_l.addWidget(self.audit)
        audit_box.setLayout(audit_l)

        # Two rows so the per-vial actions don't form one wide strip and
        # so semantically related buttons stay grouped. Row 1: lifecycle
        # (Flip / Decommission / Reactivate). Row 2: outputs + bulk ops
        # (Multiply / Print / Export lineage CSV).
        self.flip_btn = QPushButton("Flip")
        self.decommission_btn = QPushButton("Decommission")
        self.reactivate_btn = QPushButton("Reactivate")
        self.multiply_btn = QPushButton("Multiply…")
        self.multiply_btn.setToolTip(
            f"Flip this vial into multiple children at once "
            f"({MULTIPLY_MIN}–{MULTIPLY_MAX}). Same genotype/owner/unit; "
            "each child gets its own label."
        )
        self.print_btn = QPushButton("Print")
        self.export_lineage_btn = QPushButton("Export lineage CSV…")
        self._all_btns = (
            self.flip_btn,
            self.decommission_btn,
            self.reactivate_btn,
            self.multiply_btn,
            self.print_btn,
            self.export_lineage_btn,
        )
        for b in self._all_btns:
            b.setEnabled(False)
        btns_row1 = QHBoxLayout()
        for b in (self.flip_btn, self.decommission_btn, self.reactivate_btn):
            btns_row1.addWidget(b)
        btns_row2 = QHBoxLayout()
        for b in (self.multiply_btn, self.print_btn, self.export_lineage_btn):
            btns_row2.addWidget(b)
        self.export_lineage_btn.clicked.connect(self._export_lineage)
        self.print_btn.clicked.connect(self._print_label)
        self.flip_btn.clicked.connect(self._flip)
        self.decommission_btn.clicked.connect(self._decommission)
        self.reactivate_btn.clicked.connect(self._reactivate)
        self.multiply_btn.clicked.connect(self._multiply)

        layout = QVBoxLayout(self)
        layout.addWidget(self.header)
        layout.addWidget(form_box)
        layout.addWidget(audit_box)
        layout.addLayout(btns_row1)
        layout.addLayout(btns_row2)
        layout.addStretch()

    @Slot(object, str)
    def show_detail(self, detail: VialDetail | None, raw: str) -> None:
        if detail is None:
            self.header.setText(
                f"<b>Unknown payload</b> — not in this database:<br><code>{raw}</code>"
            )
            self._clear_fields()
            return

        r = detail.row
        status = "ACTIVE" if r.is_active else "decommissioned"
        self.header.setText(f"<h2>{r.print_code}</h2>")
        self.print_code_lbl.setText(r.print_code)
        self.status_lbl.setText(status)
        self.generation_lbl.setText(str(r.generation))
        self.genotype_lbl.setText(r.genotype_name)
        self.notation_lbl.setText(r.genotype_notation)
        self.phenotype_lbl.setText(r.phenotype or "-")
        self.owner_lbl.setText(f"{r.owner_username or '-'} ({r.owner_full_name or '-'})")
        self.unit_lbl.setText(r.org_unit_name or "-")
        self.donor_lbl.setText(f"{r.donor_name or '-'}  strain#{r.donor_strain_id or '-'}")
        self.created_lbl.setText(r.created_at.isoformat(timespec="seconds"))

        lineage_parts: list[str] = []
        if detail.parent_flip_print_code:
            lineage_parts.append(f"← {detail.parent_flip_print_code}")
        if detail.parent_cross_print_codes:
            lineage_parts.append("× " + ", ".join(detail.parent_cross_print_codes))
        if detail.child_flip_print_codes:
            lineage_parts.append("→ " + ", ".join(detail.child_flip_print_codes))
        self.lineage_lbl.setText(" · ".join(lineage_parts) if lineage_parts else "-")

        lines = []
        for a in detail.audit:
            lines.append(
                f"{a.created_at.isoformat(timespec='seconds')}  "
                f"{a.action:<14} by {a.actor_username or '-'}"
            )
        self.audit.setPlainText("\n".join(lines) or "(no audit events)")

        self._current_vial_id = r.vial_id
        self._current_print_code = r.print_code
        self.export_lineage_btn.setEnabled(True)
        self.flip_btn.setEnabled(r.is_active)
        self.decommission_btn.setEnabled(r.is_active)
        # Multiply is a 1-to-N flip, so it has the same precondition as Flip.
        self.multiply_btn.setEnabled(r.is_active)
        # Reactivate is the inverse of decommission — only offered when
        # the vial is currently inactive.
        self.reactivate_btn.setEnabled(not r.is_active)
        # Print only if the label PNG still exists AND printer is enabled.
        label_path = settings.data_dir / "labels" / f"{r.print_code}.png"
        self.print_btn.setEnabled(settings.printer_enabled and label_path.exists())

    def _clear_fields(self) -> None:
        for lbl in (
            self.print_code_lbl,
            self.status_lbl,
            self.generation_lbl,
            self.genotype_lbl,
            self.notation_lbl,
            self.phenotype_lbl,
            self.owner_lbl,
            self.unit_lbl,
            self.donor_lbl,
            self.created_lbl,
            self.lineage_lbl,
        ):
            lbl.setText("-")
        self.audit.clear()
        self._current_vial_id = None
        self._current_print_code = None
        for b in self._all_btns:
            b.setEnabled(False)

    def _print_label(self) -> None:
        if self._current_print_code is None:
            return
        label_path = settings.data_dir / "labels" / f"{self._current_print_code}.png"
        if not label_path.exists():
            QMessageBox.warning(
                self,
                "Label missing",
                f"No label PNG at {label_path}.\nRe-create or flip the vial to regenerate it.",
            )
            return

        # A single Print click treats SKIP and CANCEL the same — both
        # abort. Only PROCEED sends bytes.
        if check_printer_or_ask(self) is not ReconnectChoice.PROCEED:
            return

        # Lazy import so the GUI module still imports when brother_ql is absent.
        from ddb.printing.service import PrinterError, print_png

        self.print_btn.setEnabled(False)
        monitor = shared_monitor()
        try:
            result = print_png(label_path.read_bytes())
        except PrinterError as e:
            QMessageBox.critical(self, "Printer error", str(e))
            self.print_btn.setEnabled(True)
            if monitor is not None:
                monitor.force_probe()  # something went wrong — re-check
            return
        except (OSError, ConnectionError) as e:
            QMessageBox.critical(self, "Printer unreachable", str(e))
            self.print_btn.setEnabled(True)
            if monitor is not None:
                monitor.force_probe()
            return
        QMessageBox.information(self, "Printed", result.summary())
        self.print_btn.setEnabled(True)

    def _export_lineage(self) -> None:
        if self._current_vial_id is None:
            return
        with Session(engine) as s:
            v = s.get(Vial, self._current_vial_id)
            if v is None:
                return
            suggested = f"lineage_{v.print_code}.csv"
            out, _ = QFileDialog.getSaveFileName(self, "Save lineage CSV", suggested, "CSV (*.csv)")
            if not out:
                return
            path = export_lineage_csv(s, v.id, Path(out))
        QMessageBox.information(self, "Lineage exported", f"Wrote {path}")

    def _flip(self) -> None:
        """Decommission the loaded vial and create a successor.

        Mirrors `CreateVialDialog`'s printer-gate pattern: if the user has
        auto-print enabled, the new label is sent to the printer in the
        same click. Failure to print does not roll back the DB row — the
        new vial is persistent and re-printable from this panel.
        """
        if self._current_print_code is None or self._current_vial_id is None:
            return
        old_code = self._current_print_code

        confirm = QMessageBox.question(
            self,
            "Flip vial?",
            f"Flip {old_code}?\n\n"
            "This decommissions the current vial and creates a successor "
            "with the same genotype, owner, and org unit.",
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
                created = flip_vial(
                    s, old_print_code=old_code, actor_id=current_user_id(s)
                )
                new_vial_id = created.vial.id
                new_code = created.vial.print_code
                label_path = Path(str(created.label_path))
        except VialNotFoundError as e:
            QMessageBox.warning(self, "Flip failed", str(e))
            self.flip_btn.setEnabled(True)
            return
        except WorkflowError as e:
            QMessageBox.critical(self, "Flip failed", str(e))
            self.flip_btn.setEnabled(True)
            return

        printed = False
        print_error: str | None = None
        if do_print and label_path.exists():
            from ddb.printing.service import PrinterError, print_png

            monitor = shared_monitor()
            try:
                print_png(label_path.read_bytes())
                printed = True
            except (PrinterError, OSError, ConnectionError) as e:
                print_error = str(e)
                if monitor is not None:
                    monitor.force_probe()

        with Session(engine) as s:
            detail = vial_detail(s, new_vial_id)
        if detail is not None:
            self.show_detail(detail, f"flip {old_code} → {new_code}")

        suffix = " [printed]" if printed else (" [print failed]" if print_error else "")
        self.vial_changed.emit(f"flipped {old_code} → {new_code}{suffix}")

        if print_error is not None:
            QMessageBox.warning(
                self,
                "Print failed",
                f"Vial {new_code} was created, but the label did not print:\n\n"
                f"{print_error}\n\n"
                "You can re-print from this panel once the printer is back.",
            )

    def _decommission(self) -> None:
        """Mark the loaded vial as end-of-life with no successor."""
        if self._current_print_code is None or self._current_vial_id is None:
            return
        code = self._current_print_code
        vial_id = self._current_vial_id

        reason, ok = QInputDialog.getText(
            self,
            "Decommission vial",
            f"Decommission {code}?\n\nOptional reason (e.g. 'contaminated'):",
        )
        if not ok:
            return

        self.decommission_btn.setEnabled(False)
        try:
            with Session(engine) as s:
                decommission_vial(
                    s,
                    print_code=code,
                    actor_id=current_user_id(s),
                    reason=reason.strip() or None,
                )
        except VialNotFoundError as e:
            QMessageBox.warning(self, "Decommission failed", str(e))
            self.decommission_btn.setEnabled(True)
            return
        except WorkflowError as e:
            QMessageBox.critical(self, "Decommission failed", str(e))
            self.decommission_btn.setEnabled(True)
            return

        with Session(engine) as s:
            detail = vial_detail(s, vial_id)
        if detail is not None:
            self.show_detail(detail, f"decommissioned {code}")
        self.vial_changed.emit(f"decommissioned {code}")

    def _reactivate(self) -> None:
        """Bring a decommissioned vial back to active.

        Unusual operation — pops a hard warning that calls out any active
        flip-descendants, because reactivating a vial that has an active
        successor creates a forked lineage (two live vials sharing a
        genealogy). The user must explicitly say Yes from a default-No
        dialog.
        """
        if self._current_print_code is None or self._current_vial_id is None:
            return
        code = self._current_print_code
        vial_id = self._current_vial_id

        with Session(engine) as s:
            descendants = active_flip_descendant_codes(s, vial_id)

        if descendants:
            warning = (
                f"<b>Reactivate {code}?</b><br><br>"
                "This vial was decommissioned, and there is still an active "
                "descendant in its flip-chain:<br><br>"
                f"&nbsp;&nbsp;<b>{', '.join(descendants)}</b><br><br>"
                "Reactivating now will leave both vials active and produce a "
                "<b>forked lineage</b>. Only do this if you really intend to "
                "track two parallel branches.<br><br>"
                "Continue?"
            )
        else:
            warning = (
                f"<b>Reactivate {code}?</b><br><br>"
                "Reactivation is meant for undoing an accidental decommission. "
                "The vial will return to ACTIVE state and will be selectable "
                "for flips again.<br><br>"
                "Are you sure?"
            )

        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Icon.Warning)
        msg.setWindowTitle("Reactivate vial")
        msg.setTextFormat(Qt.TextFormat.RichText)
        msg.setText(warning)
        msg.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        msg.setDefaultButton(QMessageBox.StandardButton.No)
        if msg.exec() != QMessageBox.StandardButton.Yes:
            return

        self.reactivate_btn.setEnabled(False)
        try:
            with Session(engine) as s:
                reactivate_vial(s, print_code=code, actor_id=current_user_id(s))
        except VialNotFoundError as e:
            QMessageBox.warning(self, "Reactivate failed", str(e))
            self.reactivate_btn.setEnabled(True)
            return
        except WorkflowError as e:
            QMessageBox.critical(self, "Reactivate failed", str(e))
            self.reactivate_btn.setEnabled(True)
            return

        with Session(engine) as s:
            detail = vial_detail(s, vial_id)
        if detail is not None:
            self.show_detail(detail, f"reactivated {code}")
        suffix = " (forked lineage)" if descendants else ""
        self.vial_changed.emit(f"reactivated {code}{suffix}")

    def _multiply(self) -> None:
        """Flip the loaded vial into N children at once.

        Asks for a count (2..MULTIPLY_MAX), decommissions the parent
        once, creates N successor vials sharing the same genotype/owner/
        org-unit, then prints each label sequentially with the same
        Bluetooth settle delay CreateVialDialog uses. Print failures do
        NOT roll back any DB row — failed labels can be re-printed from
        the panel once the printer is back.
        """
        if self._current_print_code is None or self._current_vial_id is None:
            return
        old_code = self._current_print_code

        count, ok = QInputDialog.getInt(
            self,
            "Multiply vial",
            (
                f"Flip {old_code} into how many vials?\n\n"
                f"Allowed range: {MULTIPLY_MIN}–{MULTIPLY_MAX}. "
                "Each child gets its own print code + label."
            ),
            MULTIPLY_DEFAULT,
            MULTIPLY_MIN,
            MULTIPLY_MAX,
            1,
        )
        if not ok:
            return

        do_print = settings.printer_enabled and settings.printer_auto_print
        if do_print:
            choice = check_printer_or_ask(self)
            if choice is ReconnectChoice.CANCEL:
                return
            do_print = choice is not ReconnectChoice.SKIP

        self.multiply_btn.setEnabled(False)
        try:
            with Session(engine) as s:
                created = multiply_vial(
                    s, old_print_code=old_code, count=count, actor_id=current_user_id(s)
                )
                children = [
                    (c.vial.id, c.vial.print_code, Path(str(c.label_path))) for c in created
                ]
        except VialNotFoundError as e:
            QMessageBox.warning(self, "Multiply failed", str(e))
            self.multiply_btn.setEnabled(True)
            return
        except WorkflowError as e:
            QMessageBox.critical(self, "Multiply failed", str(e))
            self.multiply_btn.setEnabled(True)
            return

        printed_count = 0
        failed_count = 0
        first_print_error: str | None = None
        if do_print:
            from ddb.printing.service import PrinterError, print_png

            monitor = shared_monitor()
            for i, (_, _, label_path) in enumerate(children):
                if not label_path.exists():
                    failed_count += 1
                    if first_print_error is None:
                        first_print_error = f"missing label PNG: {label_path}"
                    continue
                try:
                    print_png(label_path.read_bytes())
                    printed_count += 1
                except (PrinterError, OSError, ConnectionError) as e:
                    failed_count += 1
                    if first_print_error is None:
                        first_print_error = str(e)
                    if monitor is not None:
                        monitor.force_probe()
                if i < len(children) - 1:
                    time.sleep(_BATCH_PRINT_SETTLE_S)

        first_child_id, _, _ = children[0]
        with Session(engine) as s:
            detail = vial_detail(s, first_child_id)
        if detail is not None:
            self.show_detail(detail, f"multiply {old_code} → {count} vials")

        codes_csv = ", ".join(c[1] for c in children)
        if do_print:
            print_summary = f" [{printed_count}/{count} printed"
            if failed_count:
                print_summary += f", {failed_count} failed"
            print_summary += "]"
        else:
            print_summary = ""
        self.vial_changed.emit(f"{old_code} → {count} vials: {codes_csv}{print_summary}")

        if failed_count and first_print_error is not None:
            QMessageBox.warning(
                self,
                "Print partially failed",
                f"{failed_count} of {count} labels failed to print.\n\n"
                f"First error: {first_print_error}\n\n"
                "Vials are in the database; you can re-print individual "
                "labels from the Scan tab's detail panel.",
            )
