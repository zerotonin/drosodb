from __future__ import annotations

from datetime import UTC, datetime, timedelta

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QMainWindow, QMessageBox, QStatusBar, QTabWidget

from ddb.config import settings
from ddb.flybase import RefreshDecision, decide_refresh, paths_for, read_meta
from ddb.flybase.catalog import read_postpone, write_postpone
from ddb.gui.dialogs.flybase_download import FlybaseDownloadDialog

from .genotypes_tab import GenotypesTab
from .printer_status import PrinterStatusMonitor, install_shared_monitor
from .reports_tab import ReportsTab
from .scan_tab import ScanTab
from .settings_tab import SettingsTab


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("DDB — Drosophila Vial Tracking")
        self.resize(1200, 720)

        # Single printer-status poller shared across tabs + dialogs. It
        # runs in its own QThread and publishes state changes via Qt
        # signals. Widgets pick it up via `shared_monitor()` from
        # `printer_status.py` rather than being passed an instance.
        self.printer_monitor: PrinterStatusMonitor | None = None
        if settings.printer_enabled:
            self.printer_monitor = PrinterStatusMonitor(interval_s=60.0)
            install_shared_monitor(self.printer_monitor)
            self.printer_monitor.start()

        self.tabs = QTabWidget()
        self.scan_tab = ScanTab()
        self.reports_tab = ReportsTab()
        self.genotypes_tab = GenotypesTab()
        self.settings_tab = SettingsTab()
        self.tabs.addTab(self.scan_tab, "Scan")
        self.tabs.addTab(self.reports_tab, "Reports")
        self.tabs.addTab(self.genotypes_tab, "Genotypes")
        self.tabs.addTab(self.settings_tab, "Settings")

        # Flip the snapshot-button visibility live when the user toggles
        # debug mode in Settings — saves a restart.
        self.settings_tab.debug_changed.connect(self.scan_tab.snapshot_btn.setVisible)
        # Reflect default-camera changes immediately in the Scan-tab combo.
        self.settings_tab.default_camera_changed.connect(self._on_default_camera_changed)
        # Show/hide the Genotypes-tab Import button when the catalog
        # master toggle flips, so the user doesn't have to restart.
        self.settings_tab.catalog_enabled_changed.connect(self.genotypes_tab.set_import_visible)

        # Reload reports + genotypes when the tab becomes visible so data
        # stays fresh after creating/editing from elsewhere in the app.
        self.tabs.currentChanged.connect(self._on_tab_changed)
        self.setCentralWidget(self.tabs)

        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("Ready.")

        # Deferred: ask about catalog refresh after the window is on
        # screen so the user sees DDB first, not a surprise dialog.
        QTimer.singleShot(500, self._check_catalog_refresh)

    def _on_default_camera_changed(self, role: str) -> None:
        idx = self.scan_tab.role_box.findText(role)
        if idx >= 0:
            self.scan_tab.role_box.setCurrentIndex(idx)

    def _on_tab_changed(self, index: int) -> None:
        widget = self.tabs.widget(index)
        if widget is self.reports_tab:
            self.reports_tab._refresh()  # re-run the currently-selected preset
        elif widget is self.genotypes_tab:
            # Genotype list needs a refresh to show vial-count changes.
            self.genotypes_tab.reload()

    # ------------------------------------------------------------------
    # FlyBase catalog refresh prompt
    # ------------------------------------------------------------------

    def _check_catalog_refresh(self) -> None:
        """Consult the scheduler; open the appropriate prompt if any."""
        paths = paths_for(settings.data_dir)
        meta = read_meta(paths)
        decision = decide_refresh(
            enabled=settings.flybase_enabled,
            mode=settings.flybase_refresh_mode,
            catalog_downloaded_at=meta.downloaded_at if meta is not None else None,
            postponed_until=read_postpone(paths),
            now=datetime.now(UTC),
        )
        if decision is RefreshDecision.PROMPT_MISSING:
            self._prompt_missing_catalog(paths)
        elif decision is RefreshDecision.PROMPT_STALE and meta is not None:
            self._prompt_stale_catalog(paths, meta)
        # NOTHING and POSTPONED → stay quiet.

    def _prompt_missing_catalog(self, paths) -> None:
        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Icon.Information)
        msg.setWindowTitle("FlyBase catalog")
        msg.setText(
            "The FlyBase genotype catalog hasn't been downloaded yet. "
            "It lets you import stocks by ID (Bloomington / Vienna / Kyoto / …). "
            "Download now (~3 MB)?"
        )
        dl_btn = msg.addButton("Download", QMessageBox.ButtonRole.AcceptRole)
        msg.addButton("Later (7 days)", QMessageBox.ButtonRole.DestructiveRole)
        msg.addButton("Not now", QMessageBox.ButtonRole.RejectRole)
        msg.exec()
        clicked = msg.clickedButton()
        if clicked is dl_btn:
            FlybaseDownloadDialog(paths, parent=self).exec()
            self.settings_tab._refresh_catalog_ui()
        elif clicked is not None and clicked.text().startswith("Later"):
            write_postpone(paths, datetime.now(UTC) + timedelta(days=7))

    def _prompt_stale_catalog(self, paths, meta) -> None:
        age_days = (datetime.now(UTC) - meta.downloaded_at).days
        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Icon.Question)
        msg.setWindowTitle("FlyBase catalog — update?")
        msg.setText(
            f"Your FlyBase catalog (release <b>{meta.release}</b>) is "
            f"{age_days} days old. Check for a newer release and update now?"
        )
        update_btn = msg.addButton("Update", QMessageBox.ButtonRole.AcceptRole)
        msg.addButton("Postpone 1 day", QMessageBox.ButtonRole.DestructiveRole)
        msg.addButton("Postpone 7 days", QMessageBox.ButtonRole.DestructiveRole)
        msg.addButton("Not now", QMessageBox.ButtonRole.RejectRole)
        msg.exec()
        clicked = msg.clickedButton()
        if clicked is update_btn:
            FlybaseDownloadDialog(paths, parent=self).exec()
            self.settings_tab._refresh_catalog_ui()
        elif clicked is not None and clicked.text() == "Postpone 1 day":
            write_postpone(paths, datetime.now(UTC) + timedelta(days=1))
        elif clicked is not None and clicked.text() == "Postpone 7 days":
            write_postpone(paths, datetime.now(UTC) + timedelta(days=7))
        # "Not now" just closes; next startup will re-prompt.

    # ------------------------------------------------------------------

    def closeEvent(self, event) -> None:  # noqa: N802
        self.scan_tab.shutdown()
        if self.printer_monitor is not None:
            self.printer_monitor.stop()
            self.printer_monitor.wait(3000)
        super().closeEvent(event)
