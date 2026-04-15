from __future__ import annotations

from PySide6.QtWidgets import QMainWindow, QStatusBar, QTabWidget

from .scan_tab import ScanTab
from .tabs_placeholder import build_genotypes_tab, build_reports_tab, build_settings_tab


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("DDB — Drosophila Vial Tracking")
        self.resize(1200, 720)

        self.tabs = QTabWidget()
        self.scan_tab = ScanTab()
        self.tabs.addTab(self.scan_tab, "Scan")
        self.tabs.addTab(build_reports_tab(), "Reports")
        self.tabs.addTab(build_genotypes_tab(), "Genotypes")
        self.tabs.addTab(build_settings_tab(), "Settings")
        self.setCentralWidget(self.tabs)

        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("Ready.")

    def closeEvent(self, event) -> None:  # noqa: N802
        self.scan_tab.shutdown()
        super().closeEvent(event)
