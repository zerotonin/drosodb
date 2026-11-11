# ╔══════════════════════════════════════════════════════════════════╗
# ║  DDB — gui.dialogs.audit_trail                                   ║
# ║  « the full history of one vial »                                ║
# ╠══════════════════════════════════════════════════════════════════╣
# ║  Header with the vial's identity and lineage links, a table      ║
# ║  of every audit event, CSV export, and a jump to the             ║
# ║  genealogy chart.                                                ║
# ╚══════════════════════════════════════════════════════════════════╝
"""Audit-trail dialog for one vial."""

from __future__ import annotations

import html
from datetime import UTC, datetime
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from sqlmodel import Session

from ddb.db import engine
from ddb.gui import theme
from ddb.gui.screen import clamp_to_screen
from ddb.reports import VialDetail, vial_detail
from ddb.vial_history import TrailEntry, export_trail_csv, vial_audit_trail

PREFERRED_SIZE: tuple[int, int] = (860, 580)
COLUMNS = ("When", "Who", "Event", "Details")


def show_audit_trail(parent: QWidget, vial_id: int, *, allow_genealogy: bool = True) -> None:
    """Load a vial's history and show it modally; warn if the vial is gone.

    Args:
        parent:          Dialog parent (its window gets the modal backdrop).
        vial_id:         Vial to show.
        allow_genealogy: Offer the "Genealogy…" button. Off when opened from
                         the genealogy chart, so dialogs don't nest forever.
    """
    with Session(engine) as session:
        detail = vial_detail(session, vial_id)
        entries = vial_audit_trail(session, vial_id)
    if detail is None:
        QMessageBox.warning(parent, "Audit trail", f"No vial with id {vial_id}.")
        return
    AuditTrailDialog(detail, entries, parent, allow_genealogy=allow_genealogy).exec()


def format_local(when: datetime) -> str:
    """Stored timestamps are naive UTC; show them in the tablet's local time."""
    aware = when if when.tzinfo is not None else when.replace(tzinfo=UTC)
    return aware.astimezone().strftime("%Y-%m-%d %H:%M")


class AuditTrailDialog(QDialog):
    """Vial identity on top, every audit event below, oldest first."""

    def __init__(
        self,
        detail: VialDetail,
        entries: list[TrailEntry],
        parent: QWidget | None = None,
        *,
        allow_genealogy: bool = True,
    ) -> None:
        super().__init__(parent)
        self._detail = detail
        self._entries = entries
        row = detail.row
        self.setWindowTitle(f"Audit trail — {row.print_code}")
        self.setSizeGripEnabled(True)
        self.resize(*clamp_to_screen(*PREFERRED_SIZE, 0.9))

        self.table = _trail_table(entries)

        export_btn = QPushButton("Export CSV…")
        export_btn.clicked.connect(self._export_csv)
        buttons = QHBoxLayout()
        if allow_genealogy:
            genealogy_btn = QPushButton("Genealogy…")
            genealogy_btn.clicked.connect(self._open_genealogy)
            buttons.addWidget(genealogy_btn)
        buttons.addWidget(export_btn)
        buttons.addStretch()
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.reject)
        # Read-only dialog: start on Close, not on the first table cell / zoom button.
        close.button(QDialogButtonBox.StandardButton.Close).setFocus()
        buttons.addWidget(close)

        count = QLabel(f"{len(entries)} event{'s' if len(entries) != 1 else ''}, oldest first")
        count.setStyleSheet(f"color: {theme.LINEAGE_MUTED_TEXT};")

        layout = QVBoxLayout(self)
        layout.addLayout(_title_row(detail))
        layout.addLayout(_detail_form(detail))
        layout.addWidget(count)
        layout.addWidget(self.table, stretch=1)
        layout.addLayout(buttons)

    def _open_genealogy(self) -> None:
        from ddb.gui.dialogs.lineage_chart import show_lineage  # avoid import cycle

        show_lineage(self, self._detail.row.vial_id)

    def _export_csv(self) -> None:
        suggested = f"audit_{self._detail.row.print_code}.csv"
        out, _ = QFileDialog.getSaveFileName(self, "Save audit trail", suggested, "CSV (*.csv)")
        if not out:
            return
        try:
            export_trail_csv(self._entries, Path(out))
        except OSError as e:
            QMessageBox.critical(self, "Export failed", str(e))


def _title_row(detail: VialDetail) -> QHBoxLayout:
    row = detail.row
    code = QLabel(row.print_code)
    font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
    font.setPointSizeF(QFont().pointSizeF() * 1.6)
    font.setBold(True)
    code.setFont(font)
    code.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

    colour = theme.LINEAGE_ACTIVE if row.is_active else theme.LINEAGE_RETIRED
    pill = QLabel("ACTIVE" if row.is_active else "RETIRED")
    pill.setStyleSheet(
        f"background: {colour}; color: white; border-radius: 9px;"
        " padding: 2px 10px; font-weight: bold;"
    )
    title = QHBoxLayout()
    title.addWidget(code)
    title.addSpacing(10)
    title.addWidget(pill, alignment=Qt.AlignmentFlag.AlignVCenter)
    title.addStretch()
    return title


def _detail_form(detail: VialDetail) -> QFormLayout:
    row = detail.row
    form = QFormLayout()
    form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

    def add(label: str, text: str | None, *, rich: bool = False) -> None:
        if not text:
            return
        value = QLabel(text)
        value.setTextFormat(Qt.TextFormat.RichText if rich else Qt.TextFormat.PlainText)
        value.setWordWrap(True)
        value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        form.addRow(f"{label}:", value)

    notation = html.escape(row.genotype_notation or "")
    add(
        "Genotype",
        f"<b>{html.escape(row.genotype_name)}</b> &nbsp; <code>{notation}</code>",
        rich=True,
    )
    add("Generation", str(row.generation))
    add("Owner", row.owner_full_name or row.owner_username)
    add("Unit", row.org_unit_name)
    if row.donor_name or row.donor_strain_id:
        add(
            "Donor",
            " ".join(
                p
                for p in (
                    row.donor_name,
                    f"#{row.donor_strain_id}" if row.donor_strain_id else None,
                )
                if p
            ),
        )
    add("Created", format_local(row.created_at))
    if row.decommissioned_at is not None:
        add("Retired", format_local(row.decommissioned_at))
    parents = [detail.parent_flip_print_code, *detail.parent_cross_print_codes]
    children = [*detail.child_flip_print_codes, *detail.child_cross_print_codes]
    add("Parent", ", ".join(p for p in parents if p))
    add("Children", ", ".join(children))
    add("Notes", row.notes)
    return form


def _trail_table(entries: list[TrailEntry]) -> QTableWidget:
    table = QTableWidget(len(entries), len(COLUMNS))
    table.setHorizontalHeaderLabels(COLUMNS)
    table.verticalHeader().setVisible(False)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setAlternatingRowColors(True)
    table.setWordWrap(True)
    bold = QFont(table.font())
    bold.setBold(True)
    for r, entry in enumerate(entries):
        cells = (format_local(entry.when), entry.actor or "—", entry.label, entry.summary)
        for c, text in enumerate(cells):
            item = QTableWidgetItem(text)
            if c == 2:
                item.setFont(bold)
            table.setItem(r, c, item)
    header = table.horizontalHeader()
    for c in range(len(COLUMNS) - 1):
        header.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
    header.setSectionResizeMode(len(COLUMNS) - 1, QHeaderView.ResizeMode.Stretch)
    table.resizeRowsToContents()
    return table
