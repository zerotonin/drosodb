from __future__ import annotations

from PySide6.QtWidgets import QMainWindow, QStatusBar, QTabWidget

from .genotypes_tab import GenotypesTab
from .reports_tab import ReportsTab
from .scan_tab import ScanTab
from .settings_tab import SettingsTab


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("DDB — Drosophila Vial Tracking")
        self.resize(1200, 720)

        self.tabs = QTabWidget()
        self.scan_tab = ScanTab()
        self.reports_tab = ReportsTab()
        self.genotypes_tab = GenotypesTab()
        self.settings_tab = SettingsTab()
        # Flip the snapshot-button visibility live when the user toggles
        # debug mode in Settings — saves a restart.
        self.settings_tab.debug_changed.connect(self.scan_tab.snapshot_btn.setVisible)
        self.tabs.addTab(self.scan_tab, "Scan")
        self.tabs.addTab(self.reports_tab, "Reports")
        self.tabs.addTab(self.genotypes_tab, "Genotypes")
        self.tabs.addTab(self.settings_tab, "Settings")
        # Reload reports + genotypes when the tab becomes visible so data
        # stays fresh after creating/editing from elsewhere in the app.
        self.tabs.currentChanged.connect(self._on_tab_changed)
        self.setCentralWidget(self.tabs)

        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("Ready.")

    def _on_tab_changed(self, index: int) -> None:
        widget = self.tabs.widget(index)
        if widget is self.reports_tab:
            self.reports_tab._refresh()  # re-run the currently-selected preset
        elif widget is self.genotypes_tab:
            self.genotypes_tab.reload()

    def closeEvent(self, event) -> None:  # noqa: N802
        self.scan_tab.shutdown()
        super().closeEvent(event)
