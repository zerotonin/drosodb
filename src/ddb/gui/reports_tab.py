"""Reports tab — preset-button-driven search over `ddb.reports.search_vials`.

Layout:

    [Active vials] [All vials] [Decommissioned] [Mine]    Filter: [_____]
    ─────────────────────────────────────────────────────────────────────
    │ CODE   GEN  GENO        NOTATION          OWNER   DONOR   CREATED │
    │ ...                                                               │
    ─────────────────────────────────────────────────────────────────────
    N vials.

Each preset button is a one-liner call into `search_vials` — the backend
already composes arbitrary filter combinations, so adding more presets
later means one button + one method.
"""

from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)
from sqlmodel import Session

from ddb.config import settings
from ddb.db import engine
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

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Filter by genotype name…")
        self.search_edit.setClearButtonEnabled(True)

        controls = QHBoxLayout()
        for b in (self.active_btn, self.all_btn, self.decomm_btn, self.mine_btn):
            controls.addWidget(b)
        controls.addSpacing(24)
        controls.addWidget(QLabel("Filter:"))
        controls.addWidget(self.search_edit, stretch=1)

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

        layout = QVBoxLayout(self)
        layout.addLayout(controls)
        layout.addWidget(self.table)
        layout.addWidget(self.status)

        self.active_btn.clicked.connect(self._show_active)
        self.all_btn.clicked.connect(self._show_all)
        self.decomm_btn.clicked.connect(self._show_decommissioned)
        self.mine_btn.clicked.connect(self._show_mine)
        self.search_edit.textChanged.connect(self._refresh)

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
        self.status.setText(
            f"{label}{filter_msg}: {len(rows)} row{'s' if len(rows) != 1 else ''}."
        )
