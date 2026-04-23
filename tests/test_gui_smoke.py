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
        genotypes_tab,
        main_window,
        printer_status,
        reports_tab,
        scan_tab,
        settings_tab,
    )
    from ddb.gui.dialogs import printer_reconnect  # noqa: F401


def test_main_window_constructs_without_showing(monkeypatch) -> None:
    from PySide6.QtWidgets import QApplication

    from ddb.config import settings
    from ddb.gui.main_window import MainWindow

    # Force-disable the printer so the background status monitor (which
    # would otherwise try a real BT probe) doesn't get started at all.
    monkeypatch.setattr(settings, "printer_enabled", False)

    _qapp = QApplication.instance() or QApplication([])  # keep alive
    w = MainWindow()
    assert w.tabs.count() == 4
    titles = [w.tabs.tabText(i) for i in range(w.tabs.count())]
    assert titles == ["Scan", "Reports", "Genotypes", "Settings"]
    assert w.printer_monitor is None, "monitor must not start when printer is disabled"
    w.close()
    # Don't quit qapp — pytest may share it with other test functions.
    del w


def test_scan_tab_on_payload_accepts_compact_and_legacy() -> None:
    """Regression: `_on_payload` must use `print_code` (not `entity_id`,
    which was renamed when Micro QR landed). A non-raising call with both
    payload shapes proves the ParsedPayload API surface is wired right."""
    import pytest
    from PySide6.QtWidgets import QApplication

    from ddb.gui.scan_tab import ScanTab

    pytest.importorskip("sqlmodel")
    _qapp = QApplication.instance() or QApplication([])  # noqa: F841 — keep alive
    tab = ScanTab()
    # Both a compact and a legacy payload must flow through without raising,
    # even when there's no matching vial in the DB.
    tab._on_payload("DDB:NOMATCH")
    tab._on_payload("ddb:1:vial:999999?pc=NOMATCH&db=local")
    # Garbage too — should swallow the parse error and just nudge the status.
    tab._on_payload("not a ddb payload")
    tab.close()
    del tab
