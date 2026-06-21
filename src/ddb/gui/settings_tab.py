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

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)
from sqlmodel import Session, select

from ddb.config import settings
from ddb.db import engine
from ddb.flybase.catalog import paths_for, read_meta
from ddb.gui.dialogs.flybase_download import FlybaseDownloadDialog
from ddb.gui.dialogs.printer_reconnect import PrinterReconnectDialog
from ddb.gui.font_scale import (
    FONT_SCALE_DEFAULT,
    FONT_SCALE_MAX,
    FONT_SCALE_MIN,
    FONT_SCALE_STEP,
    clamp_font_scale,
)
from ddb.gui.printer_status import PrinterStatusLight, shared_monitor
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
    default_camera_changed = Signal(str)
    catalog_enabled_changed = Signal(bool)
    font_scale_changed = Signal(float)

    def __init__(self) -> None:
        super().__init__()

        layout = QVBoxLayout(self)
        layout.addWidget(self._build_font_group())
        layout.addWidget(self._build_debug_group())
        layout.addWidget(self._build_printer_group())
        layout.addWidget(self._build_catalog_group())
        layout.addWidget(self._build_camera_group())
        layout.addWidget(self._build_donor_group())
        layout.addWidget(self._build_user_group())
        layout.addWidget(self._build_unit_group())
        layout.addStretch()

    # ------------------------------------------------------------------
    # Printer status + reconnect
    # ------------------------------------------------------------------

    def _build_printer_group(self) -> QGroupBox:
        box = QGroupBox("Printer")
        self.printer_light = PrinterStatusLight(show_text=True)
        monitor = shared_monitor()
        if monitor is not None:
            self.printer_light.attach(monitor)

        self.probe_btn = QPushButton("Probe now")
        self.reconnect_btn = QPushButton("Reconnect…")
        self.probe_btn.clicked.connect(self._probe_now)
        self.reconnect_btn.clicked.connect(self._open_reconnect)
        if monitor is None:
            # Printer disabled in settings — the reconnect dialog has
            # nothing meaningful to offer.
            self.probe_btn.setEnabled(False)
            self.reconnect_btn.setEnabled(False)

        row = QHBoxLayout()
        row.addWidget(self.printer_light)
        row.addStretch()
        row.addWidget(self.probe_btn)
        row.addWidget(self.reconnect_btn)
        lo = QVBoxLayout(box)
        lo.addLayout(row)
        return box

    def _probe_now(self) -> None:
        monitor = shared_monitor()
        if monitor is not None:
            monitor.force_probe()

    def _open_reconnect(self) -> None:
        monitor = shared_monitor()
        if monitor is None:
            return
        PrinterReconnectDialog(monitor.last_status, self).exec()
        # Always re-probe after the dialog closes so the light reflects
        # whatever state the printer is in now.
        monitor.force_probe()

    # ------------------------------------------------------------------
    # Genotype catalog (FlyBase)
    # ------------------------------------------------------------------

    def _build_catalog_group(self) -> QGroupBox:
        box = QGroupBox("Genotype catalog")

        # Row 1 — master toggle + help button.
        self.catalog_chk = QCheckBox("Import genotype data from FlyBase")
        self.catalog_chk.setChecked(settings.flybase_enabled)
        self.catalog_chk.toggled.connect(self._on_catalog_toggled)
        help_btn = QPushButton("?")
        help_btn.setFixedWidth(28)
        help_btn.setToolTip("Which stock collections does this catalog cover?")
        help_btn.clicked.connect(self._on_catalog_help)
        row1 = QHBoxLayout()
        row1.addWidget(self.catalog_chk)
        row1.addWidget(help_btn)
        row1.addStretch()

        # Row 2 — status line (release + age + row count).
        self.catalog_status_lbl = QLabel()
        self.catalog_status_lbl.setStyleSheet("color: #666;")
        self.catalog_status_lbl.setWordWrap(True)

        # Row 3 — refresh mode radio group.
        self.catalog_mode_group = QButtonGroup(self)
        self.catalog_mode_manual = QRadioButton("Manual")
        self.catalog_mode_weekly = QRadioButton("Weekly")
        self.catalog_mode_monthly = QRadioButton("Monthly")
        for mode, rb in (
            ("manual", self.catalog_mode_manual),
            ("weekly", self.catalog_mode_weekly),
            ("monthly", self.catalog_mode_monthly),
        ):
            self.catalog_mode_group.addButton(rb)
            if settings.flybase_refresh_mode == mode:
                rb.setChecked(True)
        if not self.catalog_mode_group.checkedButton():
            self.catalog_mode_manual.setChecked(True)
        self.catalog_mode_manual.toggled.connect(
            lambda b: b and self._on_refresh_mode_changed("manual")
        )
        self.catalog_mode_weekly.toggled.connect(
            lambda b: b and self._on_refresh_mode_changed("weekly")
        )
        self.catalog_mode_monthly.toggled.connect(
            lambda b: b and self._on_refresh_mode_changed("monthly")
        )
        row3 = QHBoxLayout()
        row3.addWidget(QLabel("Refresh:"))
        row3.addWidget(self.catalog_mode_manual)
        row3.addWidget(self.catalog_mode_weekly)
        row3.addWidget(self.catalog_mode_monthly)
        row3.addStretch()

        # Row 4 — download / check buttons.
        self.catalog_download_btn = QPushButton("Download now")
        self.catalog_check_btn = QPushButton("Check for update")
        self.catalog_download_btn.clicked.connect(self._on_download_catalog)
        self.catalog_check_btn.clicked.connect(self._on_check_catalog_update)
        row4 = QHBoxLayout()
        row4.addWidget(self.catalog_download_btn)
        row4.addWidget(self.catalog_check_btn)
        row4.addStretch()

        # Row 5 — file path display (monospace, selectable — user can
        # delete the file manually if they want).
        self.catalog_path_lbl = QLabel()
        self.catalog_path_lbl.setStyleSheet("font-family: monospace; color: #666;")
        self.catalog_path_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.catalog_path_lbl.setWordWrap(True)

        lo = QVBoxLayout(box)
        lo.addLayout(row1)
        lo.addWidget(self.catalog_status_lbl)
        lo.addLayout(row3)
        lo.addLayout(row4)
        lo.addWidget(self.catalog_path_lbl)

        self._refresh_catalog_ui()
        return box

    def _refresh_catalog_ui(self) -> None:
        """Recompute status line + path label from the current on-disk meta.
        Call after a download completes, or after the checkbox toggles."""
        paths = paths_for(settings.data_dir)
        self.catalog_path_lbl.setText(f"File: {paths.tsv_gz}")

        meta = read_meta(paths)
        if meta is None:
            self.catalog_status_lbl.setText(
                "<i>Catalog not downloaded yet.</i>"
                if settings.flybase_enabled
                else "<i>Disabled.</i>"
            )
        else:
            from datetime import UTC, datetime

            now = datetime.now(UTC)
            age = now - meta.downloaded_at
            age_days = age.days
            if age_days == 0:
                age_text = "today"
            else:
                age_text = f"{age_days} day{'s' if age_days != 1 else ''} ago"
            self.catalog_status_lbl.setText(
                f"Release <b>{meta.release}</b> · {meta.row_count:,} stocks · "
                f"downloaded {age_text}."
            )

        # Enable/disable child controls based on master toggle.
        enabled = settings.flybase_enabled
        for w in (
            self.catalog_mode_manual,
            self.catalog_mode_weekly,
            self.catalog_mode_monthly,
            self.catalog_download_btn,
            self.catalog_check_btn,
        ):
            w.setEnabled(enabled)

    def _on_catalog_toggled(self, checked: bool) -> None:
        settings.flybase_enabled = checked
        try:
            _upsert_env_var(_env_path(), "DDB_FLYBASE_ENABLED", "1" if checked else "0")
        except OSError as e:
            QMessageBox.warning(
                self, "Could not save .env", f"Setting applied in-session but not persisted: {e}"
            )
        self._refresh_catalog_ui()
        self.catalog_enabled_changed.emit(checked)

    def _on_refresh_mode_changed(self, mode: str) -> None:
        settings.flybase_refresh_mode = mode
        try:
            _upsert_env_var(_env_path(), "DDB_FLYBASE_REFRESH_MODE", mode)
        except OSError as e:
            QMessageBox.warning(
                self, "Could not save .env", f"Setting applied in-session but not persisted: {e}"
            )

    def _on_download_catalog(self) -> None:
        paths = paths_for(settings.data_dir)
        ok = QMessageBox.question(
            self,
            "Download FlyBase catalog?",
            "Download the current FlyBase stocks catalog?<br><br>"
            "~3 MB, one file, covering Bloomington, Vienna (VDRC), Kyoto, "
            "NIG-Fly, KDRC, FlyORF, and NDSSC. You can refresh it later from "
            "this same panel.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if ok != QMessageBox.StandardButton.Yes:
            return
        dlg = FlybaseDownloadDialog(paths, parent=self)
        dlg.exec()
        self._refresh_catalog_ui()

    def _on_check_catalog_update(self) -> None:
        """Compare the local release to the remote index; prompt if newer."""
        from ddb.flybase.catalog import discover_current_release

        paths = paths_for(settings.data_dir)
        meta = read_meta(paths)

        try:
            info = discover_current_release(timeout_s=10.0)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(
                self,
                "Could not check for updates",
                f"FlyBase's release index is unreachable: {e}",
            )
            return

        if meta is not None and meta.release == info.release:
            QMessageBox.information(
                self,
                "Catalog up to date",
                f"You have the latest release: <b>{info.release}</b>.",
            )
            return

        current_txt = meta.release if meta is not None else "(none)"
        ok = QMessageBox.question(
            self,
            "New catalog available",
            f"A newer release is available: <b>{info.release}</b>.<br>"
            f"Your current release: <code>{current_txt}</code>.<br><br>"
            "Download it now?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if ok != QMessageBox.StandardButton.Yes:
            return
        dlg = FlybaseDownloadDialog(paths, release=info, parent=self)
        dlg.exec()
        self._refresh_catalog_ui()

    def _on_catalog_help(self) -> None:
        """List the collections covered by the currently-downloaded catalog.
        Driven by real meta so it can't drift from what's on disk."""
        paths = paths_for(settings.data_dir)
        meta = read_meta(paths)
        if meta is None:
            QMessageBox.information(
                self,
                "Genotype catalog — coverage",
                "No catalog downloaded yet. Click <b>Download now</b> above to "
                "fetch the current FlyBase release; the list of collections "
                "will appear here afterwards.",
            )
            return
        rows = sorted(meta.collection_counts.items(), key=lambda kv: (-kv[1], kv[0]))
        html = (
            "<b>Available stock collections in this catalog</b> "
            f"(release {meta.release}):<br><br><table>"
            + "".join(
                f"<tr><td>{name}</td><td>&nbsp;&nbsp;</td><td>{n:,} stocks</td></tr>"
                for name, n in rows
            )
            + "</table><br>"
            "Enter a stock-center ID in the Import dialog, or a FlyBase "
            "FBst number for direct lookup."
        )
        QMessageBox.information(self, "Genotype catalog — coverage", html)

    # ------------------------------------------------------------------
    # Default camera for scanning
    # ------------------------------------------------------------------

    def _build_camera_group(self) -> QGroupBox:
        box = QGroupBox("Scanner")
        self.camera_box = QComboBox()
        self.camera_box.addItems(["back", "front"])
        idx = self.camera_box.findText(settings.default_camera_role)
        if idx >= 0:
            self.camera_box.setCurrentIndex(idx)
        self.camera_box.currentTextChanged.connect(self._on_default_camera_changed)

        tip = QLabel(
            "<i>Which camera the Scan tab uses by default. You can still "
            "switch per-session via the combo on the Scan tab.</i>"
        )
        tip.setStyleSheet("color: #666;")

        form = QFormLayout()
        form.addRow("Default camera:", self.camera_box)
        lo = QVBoxLayout(box)
        lo.addLayout(form)
        lo.addWidget(tip)
        return box

    def _on_default_camera_changed(self, role: str) -> None:
        settings.default_camera_role = role
        try:
            _upsert_env_var(_env_path(), "DDB_DEFAULT_CAMERA_ROLE", role)
        except OSError as e:
            QMessageBox.warning(
                self,
                "Could not save .env",
                f"Setting applied in-session but not persisted: {e}",
            )
        self.default_camera_changed.emit(role)

    # ------------------------------------------------------------------
    # Font size — slider + spinbox; persists to .env; live retune
    # ------------------------------------------------------------------

    def _build_font_group(self) -> QGroupBox:
        box = QGroupBox("Font size")

        current = clamp_font_scale(settings.gui_font_scale)
        # Slider is integer-only — encode 0.7..2.0 as 70..200 (×100).
        self.font_slider = QSlider(Qt.Orientation.Horizontal)
        self.font_slider.setRange(int(FONT_SCALE_MIN * 100), int(FONT_SCALE_MAX * 100))
        self.font_slider.setSingleStep(int(FONT_SCALE_STEP * 100))
        self.font_slider.setPageStep(int(FONT_SCALE_STEP * 100 * 2))
        self.font_slider.setValue(int(current * 100))

        self.font_spin = QDoubleSpinBox()
        self.font_spin.setRange(FONT_SCALE_MIN, FONT_SCALE_MAX)
        self.font_spin.setSingleStep(FONT_SCALE_STEP)
        self.font_spin.setDecimals(2)
        self.font_spin.setSuffix("×")
        self.font_spin.setValue(current)

        reset_btn = QPushButton("Reset")
        reset_btn.setToolTip(f"Restore the default scale ({FONT_SCALE_DEFAULT:.2f}×).")
        reset_btn.clicked.connect(lambda: self._set_font_scale_from_user(FONT_SCALE_DEFAULT))

        self.font_slider.valueChanged.connect(lambda v: self._set_font_scale_from_user(v / 100.0))
        self.font_spin.valueChanged.connect(self._set_font_scale_from_user)

        tip = QLabel(
            "<i>Scales every label, table, and dialog in DDB. Live — "
            "no restart needed. Shortcuts: <b>Ctrl+=</b> bigger, "
            "<b>Ctrl+−</b> smaller, <b>Ctrl+0</b> reset.</i>"
        )
        tip.setStyleSheet("color: #666;")
        tip.setWordWrap(True)

        row = QHBoxLayout()
        row.addWidget(QLabel("Scale:"))
        row.addWidget(self.font_slider, 1)
        row.addWidget(self.font_spin)
        row.addWidget(reset_btn)

        lo = QVBoxLayout(box)
        lo.addLayout(row)
        lo.addWidget(tip)
        return box

    def _set_font_scale_from_user(self, scale: float) -> None:
        """Slider/spinbox/Reset → notify MainWindow via signal.
        MainWindow does the actual apply + persist + status-bar message and
        calls back into `set_font_scale_silently` to keep widgets in sync."""
        clamped = clamp_font_scale(scale)
        # Avoid firing on a noop (also prevents echo from set_font_scale_silently).
        if abs(clamped - settings.gui_font_scale) < 1e-3:
            return
        self.font_scale_changed.emit(clamped)

    def set_font_scale_silently(self, scale: float) -> None:
        """Reflect the current scale in the widgets without re-emitting.
        Called by MainWindow after a keyboard shortcut adjusts the scale."""
        clamped = clamp_font_scale(scale)
        for w in (self.font_slider, self.font_spin):
            w.blockSignals(True)
        try:
            self.font_slider.setValue(int(clamped * 100))
            self.font_spin.setValue(clamped)
        finally:
            for w in (self.font_slider, self.font_spin):
                w.blockSignals(False)

    def persist_font_scale(self, scale: float) -> None:
        """Write the scale back to .env so it survives restart."""
        _upsert_env_var(_env_path(), "DDB_GUI_FONT_SCALE", f"{clamp_font_scale(scale):.2f}")

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
