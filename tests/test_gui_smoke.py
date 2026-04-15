"""Smoke-test the GUI without actually opening a window.

Skips entirely if PySide6 isn't installed (so CI stays green in a pip-only
environment). When PySide6 IS present, we:

  - import every GUI module to catch syntax errors / missing imports
  - instantiate MainWindow + all tabs under the offscreen Qt platform
    so we know the widget tree builds without exceptions
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def test_gui_modules_importable() -> None:
    import ddb.gui  # noqa: F401
    from ddb.gui import (  # noqa: F401
        app,
        camera_widget,
        frame_grabber,
        main_window,
        scan_tab,
        tabs_placeholder,  # noqa: F401
    )


def test_main_window_constructs_without_showing() -> None:
    from PySide6.QtWidgets import QApplication

    from ddb.gui.main_window import MainWindow

    _qapp = QApplication.instance() or QApplication([])  # keep alive
    w = MainWindow()
    assert w.tabs.count() == 4
    titles = [w.tabs.tabText(i) for i in range(w.tabs.count())]
    assert titles == ["Scan", "Reports", "Genotypes", "Settings"]
    w.close()
    # Don't quit qapp — pytest may share it with other test functions.
    del w
