"""Settings tab — debug toggle + quick-entry masks for donors / users / org units.

Persistence:
  - The debug toggle writes / updates `DDB_GUI_DEBUG=0/1` in the project
    `.env` so the setting survives restart. It also updates the in-memory
    `settings.gui_debug` immediately and emits `debug_changed` so the Scan
    tab can show / hide the snapshot button without a restart.
  - The three entry forms commit straight to the DB.

The Settings tab is the right place to keep everything that's *about*
the system rather than *about* a particular vial / genotype.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from sqlmodel import Session, select

from ddb.config import settings
from ddb.db import engine
from ddb.models import Donor, OrgUnit, User


def _env_path() -> Path:
    """Locate the project .env — relative to the current working directory."""
    return Path(".env").resolve()


def _upsert_env_var(path: Path, key: str, value: str) -> None:
    """Write or replace `KEY=VALUE` in the .env file. Creates it if missing."""
    lines: list[str] = []
    if path.exists():
        lines = path.read_text().splitlines()
    new_line = f"{key}={value}"
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(f"{key}="):
            lines[i] = new_line
            break
    else:
        lines.append(new_line)
    path.write_text("\n".join(lines) + "\n")


class SettingsTab(QWidget):
    """Debug toggle + quick-entry masks. Emits signals when state changes."""

    debug_changed = Signal(bool)

    def __init__(self) -> None:
        super().__init__()

        layout = QVBoxLayout(self)
        layout.addWidget(self._build_debug_group())
        layout.addWidget(self._build_donor_group())
        layout.addWidget(self._build_user_group())
        layout.addWidget(self._build_unit_group())
        layout.addStretch()

    # ------------------------------------------------------------------
    # Debug toggle
    # ------------------------------------------------------------------

    def _build_debug_group(self) -> QGroupBox:
        box = QGroupBox("Debug")
        self.debug_chk = QCheckBox("Show 'Save snapshot' button on the Scan tab")
        self.debug_chk.setChecked(settings.gui_debug)
        self.debug_chk.toggled.connect(self._on_debug_toggled)

        tip = QLabel(
            "<i>Persists to <code>.env</code> (DDB_GUI_DEBUG). Takes effect "
            "immediately on the running app.</i>"
        )
        tip.setStyleSheet("color: #666;")

        lo = QVBoxLayout(box)
        lo.addWidget(self.debug_chk)
        lo.addWidget(tip)
        return box

    def _on_debug_toggled(self, checked: bool) -> None:
        settings.gui_debug = checked  # update the singleton so fresh lookups see it
        try:
            _upsert_env_var(_env_path(), "DDB_GUI_DEBUG", "1" if checked else "0")
        except OSError as e:
            QMessageBox.warning(
                self, "Could not save .env", f"Setting applied in-session but not persisted: {e}"
            )
        self.debug_changed.emit(checked)

    # ------------------------------------------------------------------
    # Donor / User / Org-unit quick-entry masks
    # ------------------------------------------------------------------

    def _build_donor_group(self) -> QGroupBox:
        box = QGroupBox("Add donor")
        self.donor_name_edit = QLineEdit()
        self.donor_name_edit.setPlaceholderText("e.g. Bloomington Stock Center")
        self.donor_institution_edit = QLineEdit()
        self.donor_institution_edit.setPlaceholderText("Optional — institution")
        self.donor_contact_edit = QLineEdit()
        self.donor_contact_edit.setPlaceholderText("Optional — email / phone")
        self.donor_add_btn = QPushButton("Add donor")
        self.donor_add_btn.clicked.connect(self._add_donor)

        form = QFormLayout(box)
        form.addRow("Name:", self.donor_name_edit)
        form.addRow("Institution:", self.donor_institution_edit)
        form.addRow("Contact:", self.donor_contact_edit)
        form.addRow("", self.donor_add_btn)
        return box

    def _add_donor(self) -> None:
        name = self.donor_name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "Missing name", "Donor name cannot be empty.")
            return
        with Session(engine) as s:
            dupe = s.exec(select(Donor).where(Donor.name == name)).first()
            if dupe is not None:
                QMessageBox.warning(self, "Duplicate", f"A donor named {name!r} already exists.")
                return
            s.add(
                Donor(
                    name=name,
                    institution=self.donor_institution_edit.text().strip() or None,
                    contact=self.donor_contact_edit.text().strip() or None,
                )
            )
            s.commit()
        QMessageBox.information(self, "Donor added", f"Added donor {name!r}.")
        for e in (self.donor_name_edit, self.donor_institution_edit, self.donor_contact_edit):
            e.clear()

    def _build_user_group(self) -> QGroupBox:
        box = QGroupBox("Add user")
        self.user_username_edit = QLineEdit()
        self.user_username_edit.setPlaceholderText("short — e.g. bgeurten")
        self.user_fullname_edit = QLineEdit()
        self.user_fullname_edit.setPlaceholderText("e.g. Bart Geurten")
        self.user_email_edit = QLineEdit()
        self.user_email_edit.setPlaceholderText("Optional — email")
        self.user_add_btn = QPushButton("Add user")
        self.user_add_btn.clicked.connect(self._add_user)

        form = QFormLayout(box)
        form.addRow("Username:", self.user_username_edit)
        form.addRow("Full name:", self.user_fullname_edit)
        form.addRow("Email:", self.user_email_edit)
        form.addRow("", self.user_add_btn)
        return box

    def _add_user(self) -> None:
        username = self.user_username_edit.text().strip()
        if not username:
            QMessageBox.warning(self, "Missing username", "Username cannot be empty.")
            return
        with Session(engine) as s:
            dupe = s.exec(select(User).where(User.username == username)).first()
            if dupe is not None:
                QMessageBox.warning(self, "Duplicate", f"A user named {username!r} already exists.")
                return
            s.add(
                User(
                    username=username,
                    full_name=self.user_fullname_edit.text().strip() or None,
                    email=self.user_email_edit.text().strip() or None,
                )
            )
            s.commit()
        QMessageBox.information(self, "User added", f"Added user {username!r}.")
        for e in (self.user_username_edit, self.user_fullname_edit, self.user_email_edit):
            e.clear()

    def _build_unit_group(self) -> QGroupBox:
        box = QGroupBox("Add org unit")
        self.unit_name_edit = QLineEdit()
        self.unit_name_edit.setPlaceholderText("e.g. private stock Bart")
        self.unit_description_edit = QLineEdit()
        self.unit_description_edit.setPlaceholderText("Optional — description")
        self.unit_add_btn = QPushButton("Add org unit")
        self.unit_add_btn.clicked.connect(self._add_unit)

        form = QFormLayout(box)
        form.addRow("Name:", self.unit_name_edit)
        form.addRow("Description:", self.unit_description_edit)
        form.addRow("", self.unit_add_btn)
        return box

    def _add_unit(self) -> None:
        name = self.unit_name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "Missing name", "Org unit name cannot be empty.")
            return
        with Session(engine) as s:
            dupe = s.exec(select(OrgUnit).where(OrgUnit.name == name)).first()
            if dupe is not None:
                QMessageBox.warning(
                    self, "Duplicate", f"An org unit named {name!r} already exists."
                )
                return
            s.add(
                OrgUnit(
                    name=name,
                    description=self.unit_description_edit.text().strip() or None,
                )
            )
            s.commit()
        QMessageBox.information(self, "Org unit added", f"Added org unit {name!r}.")
        for e in (self.unit_name_edit, self.unit_description_edit):
            e.clear()


# Backwards-compat alias for the placeholder factory that was in
# tabs_placeholder.SettingsTab-equivalents — MainWindow imports by class.
