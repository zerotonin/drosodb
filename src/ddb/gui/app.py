"""Entry point for the PySide6 GUI.

Run via:  `ddb gui`  (Typer subcommand) — see src/ddb/cli.py.
"""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from ddb.config import settings

from .font_scale import apply_font_scale
from .main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("DDB")
    apply_font_scale(app, settings.gui_font_scale)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
