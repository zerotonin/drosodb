"""Small utility for "any edit marks the form dirty" forms.

A typical inline-edit tab wires up one handler for every editable field
so Save/Revert light up as soon as the user touches anything. Without a
helper that ends up as 10-20 lines of `widget.textChanged.connect(...)`
boilerplate per form. `DirtyTracker` collapses that to one call:

    tracker = DirtyTracker(self._mark_dirty)
    tracker.watch(
        self.form.name_edit,
        self.form.chrom_x_edit,
        ...
        self.form.wildtype_chk,
    )

Each widget is inspected once for the most useful "value changed"
signal — QLineEdit.textChanged, QComboBox.currentIndexChanged,
QCheckBox.toggled, QSpinBox.valueChanged, etc. Unknown widget types
raise so a typo in the watch list surfaces immediately.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractSpinBox,
    QComboBox,
    QLineEdit,
    QPlainTextEdit,
    QTextEdit,
    QWidget,
)


class DirtyTracker:
    """Wire a 0-arg `on_change` callback to the value-change signal of
    each watched widget. One instance per form."""

    def __init__(self, on_change: Callable[[], None]) -> None:
        self._on_change = on_change

    def watch(self, *widgets: QWidget) -> None:
        for w in widgets:
            self._watch_one(w)

    def _watch_one(self, w: QWidget) -> None:
        # Order matters: QPlainTextEdit is a QWidget but not a QLineEdit;
        # QCheckBox is a QAbstractButton. We test most-specific first.
        if isinstance(w, QLineEdit):
            w.textChanged.connect(lambda _t: self._on_change())
        elif isinstance(w, (QTextEdit, QPlainTextEdit)):
            w.textChanged.connect(self._on_change)
        elif isinstance(w, QComboBox):
            w.currentIndexChanged.connect(lambda _i: self._on_change())
        elif isinstance(w, QAbstractSpinBox):
            # Covers QSpinBox, QDoubleSpinBox, QDateTimeEdit.
            w.editingFinished.connect(self._on_change)
        elif isinstance(w, QAbstractButton):
            # QCheckBox, QRadioButton, QPushButton (rare but legal).
            w.toggled.connect(lambda _b: self._on_change())
        else:
            raise TypeError(
                f"DirtyTracker doesn't know how to watch {type(w).__name__}; "
                "add a branch in _watch_one() or pass a more specific widget."
            )
