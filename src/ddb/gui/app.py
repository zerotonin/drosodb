"""Entry point for the PySide6 GUI.

Run via:  `ddb gui`  (Typer subcommand) — see src/ddb/cli.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from ddb.config import settings

from .font_scale import apply_font_scale
from .main_window import MainWindow

# Repo-shipped app icon. Falls back gracefully when the file is missing
# (e.g. a stripped-down install) — Qt just shows the default window
# chrome icon and the app still launches.
_ICON_PATH = Path(__file__).resolve().parent.parent.parent.parent / "assets" / "ddb_logo.png"


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("DDB")
    app.setApplicationDisplayName("DDB — Drosophila vial tracker")
    # `setDesktopFileName` lets Wayland compositors match the running
    # window to the .desktop entry the launcher script installs, so the
    # taskbar shows the same icon as the desktop shortcut.
    app.setDesktopFileName("ddb")
    if _ICON_PATH.exists():
        app.setWindowIcon(QIcon(str(_ICON_PATH)))
    apply_font_scale(app, settings.gui_font_scale)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
