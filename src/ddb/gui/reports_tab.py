"""Reports tab — preset-button-driven search over `ddb.reports.search_vials`.

Layout:

    [Active vials] [All vials] [Decommissioned] [Mine]    Filter: [_____]
    ─────────────────────────────────────────────────────────────────────
    │ CODE   GEN  GENO        NOTATION          OWNER   DONOR   CREATED │
    │ ...                                                               │
    ─────────────────────────────────────────────────────────────────────
    N vials.                                [Audit trail…] [Genealogy…]

Double-clicking a row opens that vial's audit trail; "Genealogy…" shows
its ancestors and descendants as a flow chart. Both are also on the
right-click menu.

Each preset button is a one-liner call into `search_vials` — the backend
already composes arbitrary filter combinations, so adding more presets
later means one button + one method.
"""

from __future__ import annotations

from datetime import UTC, datetime

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QPoint, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)
from sqlmodel import Session

from ddb.config import settings
from ddb.db import engine
from ddb.gui.dialogs.audit_trail import show_audit_trail
from ddb.gui.dialogs.lineage_chart import show_lineage
from ddb.reports import VialRow, search_vials


class VialRowTableModel(QAbstractTableModel):
    # (header, VialRow attribute, column width hint)
    COLUMNS: tuple[tuple[str, str, int], ...] = (
        ("Code", "print_code", 70),
        ("Gen", "generation", 45),
        ("Active", "is_active", 60),
        ("Genotype", "genotype_name", 150),
        ("Notation", "genotype_notation", 260),
        ("Owner", "owner_username", 100),
        ("Unit", "org_unit_name", 120),
        ("Donor#", "donor_strain_id", 80),
        ("Created", "created_at", 120),
    )

    def __init__(self, rows: list[VialRow] | None = None) -> None:
        super().__init__()
        self._rows: list[VialRow] = rows or []

    def set_rows(self, rows: list[VialRow]) -> None:
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()

    def row_at(self, idx: int) -> VialRow | None:
        return self._rows[idx] if 0 <= idx < len(self._rows) else None

    # Qt model interface ------------------------------------------------

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.COLUMNS)

    def headerData(  # noqa: N802
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ):
        if orientation != Qt.Orientation.Horizontal or role != Qt.ItemDataRole.DisplayRole:
            return None
        return self.COLUMNS[section][0]

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or role != Qt.ItemDataRole.DisplayRole:
            return None
        row = self._rows[index.row()]
        attr = self.COLUMNS[index.column()][1]
        value = getattr(row, attr, None)
        if attr == "is_active":
            return "✓" if value else "—"
        if attr == "created_at" and value is not None:
            return value.date().isoformat()
        return "" if value is None else str(value)


class ReportsTab(QWidget):
    def __init__(self) -> None:
        super().__init__()

        self.active_btn = QPushButton("Active vials")
        self.all_btn = QPushButton("All vials")
        self.decomm_btn = QPushButton("Decommissioned")
        self.mine_btn = QPushButton(f"{settings.default_owner_username}'s vials")
        self.biosec_btn = QPushButton("Biosafety PDF…")
        self.biosec_btn.setToolTip(
            "Generate a one-staple PDF report covering snapshot counts "
            "and activity over the last 3 months (snapshot + per-owner + "
            "full active-genotype listing). Saved wherever you choose."
        )

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Filter by genotype name…")
        self.search_edit.setClearButtonEnabled(True)

        controls = QHBoxLayout()
        for b in (self.active_btn, self.all_btn, self.decomm_btn, self.mine_btn):
            controls.addWidget(b)
        controls.addSpacing(24)
        controls.addWidget(QLabel("Filter:"))
        controls.addWidget(self.search_edit, stretch=1)
        controls.addSpacing(12)
        controls.addWidget(self.biosec_btn)

        self.table = QTableView()
        self.model = VialRowTableModel()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(True)
        header = self.table.horizontalHeader()
        for col, (_, _, width) in enumerate(VialRowTableModel.COLUMNS):
            self.table.setColumnWidth(col, width)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)

        self.status = QLabel("Pick a preset above to load vials.")
        self.status.setStyleSheet("color: #666;")

        self.audit_btn = QPushButton("Audit trail…")
        self.audit_btn.setToolTip(
            "Every recorded event of the selected vial (or double-click a row)"
        )
        self.lineage_btn = QPushButton("Genealogy…")
        self.lineage_btn.setToolTip(
            "Ancestors and descendants of the selected vial as a flow chart"
        )
        footer = QHBoxLayout()
        footer.addWidget(self.status, stretch=1)
        footer.addWidget(self.audit_btn)
        footer.addWidget(self.lineage_btn)

        layout = QVBoxLayout(self)
        layout.addLayout(controls)
        layout.addWidget(self.table)
        layout.addLayout(footer)

        self.active_btn.clicked.connect(self._show_active)
        self.all_btn.clicked.connect(self._show_all)
        self.decomm_btn.clicked.connect(self._show_decommissioned)
        self.mine_btn.clicked.connect(self._show_mine)
        self.biosec_btn.clicked.connect(self._generate_biosec_pdf)
        self.search_edit.textChanged.connect(self._refresh)

        self.table.doubleClicked.connect(lambda index: self._open_audit_trail(index.row()))
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_row_menu)
        self.table.selectionModel().selectionChanged.connect(self._update_vial_buttons)
        self.model.modelReset.connect(self._update_vial_buttons)
        self.audit_btn.clicked.connect(lambda: self._open_audit_trail(self._selected_row()))
        self.lineage_btn.clicked.connect(lambda: self._open_lineage(self._selected_row()))
        self._update_vial_buttons()

        # Remember which preset is active so typing in the filter re-applies
        # it instead of resetting back to "everything".
        self._current_preset = self._show_active
        self._show_active()

    # --- Preset handlers -----------------------------------------------

    def _show_active(self) -> None:
        self._current_preset = self._show_active
        self._run("Active vials", search_vials, active=True)

    def _show_all(self) -> None:
        self._current_preset = self._show_all
        self._run("All vials", search_vials)

    def _show_decommissioned(self) -> None:
        self._current_preset = self._show_decommissioned
        self._run("Decommissioned vials", search_vials, active=False)

    def _show_mine(self) -> None:
        self._current_preset = self._show_mine
        self._run(
            f"Vials owned by {settings.default_owner_username}",
            search_vials,
            owner_username=settings.default_owner_username,
        )

    def _refresh(self) -> None:
        # Re-apply the last-pressed preset so filter edits don't lose it.
        self._current_preset()

    # --- Runner --------------------------------------------------------

    def _run(self, label: str, fn, **kwargs) -> None:
        needle = self.search_edit.text().strip() or None
        if needle:
            kwargs.setdefault("genotype_name_contains", needle)
        with Session(engine) as s:
            rows = fn(s, **kwargs)
        self.model.set_rows(rows)
        filter_msg = f" matching {needle!r}" if needle else ""
        plural = "s" if len(rows) != 1 else ""
        hint = "  Double-click a vial for its audit trail." if rows else ""
        self.status.setText(f"{label}{filter_msg}: {len(rows)} row{plural}.{hint}")

    # --- Per-vial history ----------------------------------------------

    def _selected_row(self) -> int:
        rows = self.table.selectionModel().selectedRows()
        return rows[0].row() if rows else -1

    def _update_vial_buttons(self) -> None:
        has_vial = self.model.row_at(self._selected_row()) is not None
        self.audit_btn.setEnabled(has_vial)
        self.lineage_btn.setEnabled(has_vial)

    def _open_audit_trail(self, row_index: int) -> None:
        row = self.model.row_at(row_index)
        if row is not None:
            show_audit_trail(self, row.vial_id)

    def _open_lineage(self, row_index: int) -> None:
        row = self.model.row_at(row_index)
        if row is not None:
            show_lineage(self, row.vial_id)

    def _show_row_menu(self, pos: QPoint) -> None:
        index = self.table.indexAt(pos)
        row = self.model.row_at(index.row()) if index.isValid() else None
        if row is None:
            return
        self.table.selectRow(index.row())
        menu = QMenu(self)
        menu.addAction(
            f"Audit trail of {row.print_code}…", lambda: self._open_audit_trail(index.row())
        )
        menu.addAction(f"Genealogy of {row.print_code}…", lambda: self._open_lineage(index.row()))
        menu.exec(self.table.viewport().mapToGlobal(pos))

    # --- Biosafety PDF -------------------------------------------------

    def _generate_biosec_pdf(self) -> None:
        """One-click biosafety PDF — last 3 months over the whole DB.

        CLI's `ddb report biosec` exposes the full knob set (--from / --to,
        --annual, --org-unit, --owner); this button is the always-works
        default for the user who just wants to staple something in.
        """
        # Lazy import so the GUI module still loads if reportlab is absent.
        from ddb.biosec import gather_biosec_report, period_last_n_months
        from ddb.biosec.pdf import render_biosec_pdf

        now = datetime.now(UTC)
        suggested = f"biosec_{now.date().isoformat()}.pdf"
        out, _ = QFileDialog.getSaveFileName(self, "Save biosafety PDF", suggested, "PDF (*.pdf)")
        if not out:
            return

        period_from, period_to = period_last_n_months(now, months=3)
        try:
            with Session(engine) as s:
                report = gather_biosec_report(s, from_=period_from, to=period_to, now=now)
            path = render_biosec_pdf(report, out)
        except OSError as e:
            QMessageBox.critical(self, "PDF write failed", str(e))
            return

        snap = report.snapshot
        QMessageBox.information(
            self,
            "Biosafety PDF written",
            f"Wrote {path}\n\n"
            f"Period: {period_from.date()} to {period_to.date()}\n"
            f"Active vials: {snap.active_vials}\n"
            f"In-stock genotypes: {snap.in_stock_genotypes}\n"
            f"Decommissioned vials (cumulative): {snap.decommissioned_vials}\n"
            f"Dropped genotypes: {snap.dropped_genotypes}",
        )
