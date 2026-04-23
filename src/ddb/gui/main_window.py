from __future__ import annotations

from PySide6.QtWidgets import QMainWindow, QStatusBar, QTabWidget

from ddb.config import settings

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

        # Reload reports + genotypes when the tab becomes visible so data
        # stays fresh after creating/editing from elsewhere in the app.
        self.tabs.currentChanged.connect(self._on_tab_changed)
        self.setCentralWidget(self.tabs)

        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("Ready.")

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

    def closeEvent(self, event) -> None:  # noqa: N802
        self.scan_tab.shutdown()
        if self.printer_monitor is not None:
            self.printer_monitor.stop()
            self.printer_monitor.wait(3000)
        super().closeEvent(event)
