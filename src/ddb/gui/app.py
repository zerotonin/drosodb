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
from .modal_scrim import ModalScrim

# Repo-shipped app icon. Falls back gracefully when the file is missing
# (e.g. a stripped-down install) — Qt just shows the default window
# chrome icon and the app still launches.
_ICON_PATH = Path(__file__).resolve().parent.parent.parent.parent / "assets" / "ddb_logo.png"


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("DDB")
    app.setApplicationDisplayName("DDB — Drosophila vial tracker")
    # `setDesktopFileName` lets Wayland compositors match the running
    # window to ~/.local/share/applications/ddb.desktop (written by
    # scripts/install_desktop_entry.sh), so the dock shows the DDB icon.
    app.setDesktopFileName("ddb")
    # Prefer the installed rounded-tile icon; fall back to the raw logo.
    fallback = QIcon(str(_ICON_PATH)) if _ICON_PATH.exists() else QIcon()
    app.setWindowIcon(QIcon.fromTheme("ddb", fallback))
    apply_font_scale(app, settings.gui_font_scale)
    # Dim the window behind every modal dialog. The local keeps the filter
    # alive for as long as app.exec() runs.
    modal_scrim = ModalScrim(app)  # noqa: F841
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
