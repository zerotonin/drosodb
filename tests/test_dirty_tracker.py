"""Tests for DirtyTracker.

Every branch of the widget-type dispatch is exercised with a concrete
Qt widget. Runs under the `qapp` fixture from test_gui_smoke (provided
via conftest/autouse there); we just need a QApplication to exist so
widget construction works.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QLineEdit,
    QPlainTextEdit,
    QSpinBox,
    QTextEdit,
    QWidget,
)

from ddb.gui.dirty_tracker import DirtyTracker


@pytest.fixture(scope="module")
def _qapp():
    app = QApplication.instance() or QApplication([])
    yield app


class _Counter:
    def __init__(self) -> None:
        self.n = 0

    def __call__(self) -> None:
        self.n += 1


def test_watches_line_edit(_qapp):
    c = _Counter()
    edit = QLineEdit()
    DirtyTracker(c).watch(edit)
    edit.setText("hello")
    assert c.n == 1


def test_watches_text_edit(_qapp):
    c = _Counter()
    edit = QTextEdit()
    DirtyTracker(c).watch(edit)
    edit.setPlainText("hi")
    assert c.n >= 1  # QTextEdit emits on every character sometimes


def test_watches_plain_text_edit(_qapp):
    c = _Counter()
    edit = QPlainTextEdit()
    DirtyTracker(c).watch(edit)
    edit.setPlainText("hi")
    assert c.n >= 1


def test_watches_combo_box(_qapp):
    c = _Counter()
    combo = QComboBox()
    combo.addItems(["a", "b", "c"])
    DirtyTracker(c).watch(combo)
    combo.setCurrentIndex(2)
    assert c.n == 1


def test_watches_check_box(_qapp):
    c = _Counter()
    chk = QCheckBox("thing")
    DirtyTracker(c).watch(chk)
    chk.setChecked(True)
    assert c.n == 1


def test_watches_spin_box(_qapp):
    c = _Counter()
    sp = QSpinBox()
    DirtyTracker(c).watch(sp)
    # QAbstractSpinBox.editingFinished only fires on focus/Enter, not on
    # programmatic setValue — simulate by calling the signal directly.
    sp.editingFinished.emit()
    assert c.n == 1


def test_watch_multiple_at_once(_qapp):
    c = _Counter()
    a = QLineEdit()
    b = QCheckBox()
    DirtyTracker(c).watch(a, b)
    a.setText("x")
    b.setChecked(True)
    assert c.n == 2


def test_unknown_widget_type_raises(_qapp):
    with pytest.raises(TypeError, match="doesn't know how to watch"):
        DirtyTracker(_Counter()).watch(QWidget())
