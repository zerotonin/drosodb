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
        lineage_scene,
        main_window,
        modal_scrim,
        printer_status,
        reports_tab,
        scan_tab,
        settings_tab,
        update_group,
    )
    from ddb.gui.dialogs import (  # noqa: F401
        audit_trail,
        lineage_chart,
        printer_reconnect,  # noqa: F401
    )


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


# ─────────────────────────────────────────────────────────────────
#  Modal backdrop, audit trail, genealogy
# ─────────────────────────────────────────────────────────────────


def _qapp():  # noqa: ANN202
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_modal_scrim_dims_parent_only_while_modal_dialog_open() -> None:
    from PySide6.QtWidgets import QDialog, QWidget

    from ddb.gui.modal_scrim import ModalScrim

    app = _qapp()
    scrim = ModalScrim(app)
    host = QWidget()
    host.resize(400, 300)
    host.show()
    try:
        modeless = QDialog(host)
        modeless.show()
        app.processEvents()
        assert not scrim.is_dimmed(host), "non-modal dialogs must not dim"
        modeless.close()

        modal = QDialog(host)
        modal.open()  # window-modal, non-blocking
        app.processEvents()
        assert scrim.is_dimmed(host)

        nested = QDialog(modal)
        nested.open()
        app.processEvents()
        assert scrim.is_dimmed(modal), "a dialog opened from a dialog dims that dialog"
        nested.close()
        app.processEvents()
        assert not scrim.is_dimmed(modal)
        assert scrim.is_dimmed(host)

        modal.close()
        app.processEvents()
        assert not scrim.is_dimmed(host)
    finally:
        app.removeEventFilter(scrim)
        host.close()


@pytest.fixture()
def vial_family(session, tmp_path, monkeypatch):  # noqa: ANN001, ANN201
    from ddb.config import settings
    from ddb.models import Genotype, User
    from ddb.workflows import create_vial, flip_vial, multiply_vial

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    user, geno = User(username="ella"), Genotype(name="nlg4 KO70")
    session.add_all([user, geno])
    session.commit()
    root = create_vial(session, genotype_id=geno.id, actor_id=user.id).vial
    middle = flip_vial(session, old_print_code=root.print_code, actor_id=user.id).vial
    multiply_vial(session, old_print_code=middle.print_code, count=3, actor_id=user.id)
    return session, middle


def test_genealogy_dialog_draws_every_vial_and_arrow(vial_family) -> None:  # noqa: ANN001
    from ddb.gui.dialogs.lineage_chart import LineageDialog
    from ddb.gui.lineage_scene import LinkArrow, VialCard
    from ddb.lineage import lineage_graph

    session, middle = vial_family
    _qapp()
    graph = lineage_graph(session, middle.id)
    dialog = LineageDialog(graph)
    cards = [i for i in dialog.scene.items() if isinstance(i, VialCard)]
    arrows = [i for i in dialog.scene.items() if isinstance(i, LinkArrow)]
    assert len(cards) == 5 and len(arrows) == 4
    assert dialog.scene.focus_card.node.vial_id == middle.id
    assert middle.print_code in dialog.windowTitle()
    dialog.close()


def test_audit_trail_dialog_lists_every_event(vial_family) -> None:  # noqa: ANN001
    from ddb.gui.dialogs.audit_trail import AuditTrailDialog
    from ddb.reports import vial_detail
    from ddb.vial_history import vial_audit_trail

    session, middle = vial_family
    _qapp()
    entries = vial_audit_trail(session, middle.id)
    dialog = AuditTrailDialog(vial_detail(session, middle.id), entries)
    assert dialog.table.rowCount() == len(entries) == 2
    assert dialog.table.item(1, 2).text() == "Multiplied into"
    dialog.close()


def test_reports_tab_vial_buttons_follow_selection() -> None:
    from ddb.gui.reports_tab import ReportsTab

    _qapp()
    tab = ReportsTab()
    tab.table.clearSelection()
    assert not tab.audit_btn.isEnabled() and not tab.lineage_btn.isEnabled()
    if tab.model.rowCount() > 0:
        tab.table.selectRow(0)
        assert tab.audit_btn.isEnabled() and tab.lineage_btn.isEnabled()
    tab.close()
