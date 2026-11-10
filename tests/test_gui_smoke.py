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
        update_group,
    )
    from ddb.gui.dialogs import printer_reconnect  # noqa: F401


def test_main_window_constructs_without_showing(monkeypatch) -> None:
    from PySide6.QtWidgets import QApplication

    from ddb.config import settings
    from ddb.gui.main_window import MainWindow

    # Force-disable the printer so the background status monitor (which
    # would otherwise try a real BT probe) doesn't get started at all.
    monkeypatch.setattr(settings, "printer_enabled", False)
    monkeypatch.setattr(settings, "gui_update_check", False)

    _qapp = QApplication.instance() or QApplication([])  # keep alive
    w = MainWindow()
    assert w.tabs.count() == 4
    titles = [w.tabs.tabText(i) for i in range(w.tabs.count())]
    assert titles == ["Scan", "Reports", "Genotypes", "Settings"]
    assert w.printer_monitor is None, "monitor must not start when printer is disabled"
    w.close()
    # Don't quit qapp — pytest may share it with other test functions.
    del w


def test_main_window_minimum_fits_small_screens(monkeypatch) -> None:
    """Regression: tabs sit in scroll areas, so a tall tab (Settings) can't
    force the window's minimum height past a tablet screen. Before the fix
    the minimum was ~1300 px at font scale 1.0 and ~1570 px at 1.4."""
    from PySide6.QtWidgets import QApplication, QScrollArea

    from ddb.config import settings
    from ddb.gui.main_window import MainWindow

    monkeypatch.setattr(settings, "printer_enabled", False)
    monkeypatch.setattr(settings, "gui_update_check", False)
    _qapp = QApplication.instance() or QApplication([])  # noqa: F841 — keep alive
    w = MainWindow()
    try:
        for i in range(w.tabs.count()):
            assert isinstance(w.tabs.widget(i), QScrollArea), w.tabs.tabText(i)
        assert w.minimumSizeHint().height() < 600
    finally:
        w.scan_tab.shutdown()
        w.close()


def test_update_group_off_git_shows_bare_version(tmp_path) -> None:
    """A non-git install still renders the Updates box without touching git."""
    from PySide6.QtWidgets import QApplication

    from ddb import __version__
    from ddb.gui.update_group import UpdateGroup

    _qapp = QApplication.instance() or QApplication([])  # noqa: F841 — keep alive
    box = UpdateGroup(repo=tmp_path)
    assert __version__ in box.version_lbl.text()
    assert box.check_btn.isEnabled()
    assert not box.update_btn.isEnabled()
    box.shutdown()


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
