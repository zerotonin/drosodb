"""Tests for the GUI font-size scaler.

Covers:
  - `clamp_font_scale` enforces the [0.7, 2.0] range and tolerates junk.
  - `apply_font_scale` pins the base size on the first call so subsequent
    calls are exact (1.5 → 1.0 round-trip restores the original size).
  - Out-of-range scale gets clamped, not rejected.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

from ddb.gui.font_scale import (
    FONT_SCALE_DEFAULT,
    FONT_SCALE_MAX,
    FONT_SCALE_MIN,
    apply_font_scale,
    clamp_font_scale,
    reset_base_point_size_for_tests,
)


@pytest.fixture
def qapp() -> QApplication:
    app = QApplication.instance() or QApplication([])
    reset_base_point_size_for_tests()
    return app


def test_clamp_in_range_passes_through() -> None:
    assert clamp_font_scale(1.0) == 1.0
    assert clamp_font_scale(1.5) == pytest.approx(1.5)


def test_clamp_out_of_range() -> None:
    assert clamp_font_scale(0.0) == FONT_SCALE_MIN
    assert clamp_font_scale(10.0) == FONT_SCALE_MAX
    assert clamp_font_scale(-1.0) == FONT_SCALE_MIN


def test_clamp_bad_input_falls_back_to_default() -> None:
    assert clamp_font_scale("not a number") == FONT_SCALE_DEFAULT
    assert clamp_font_scale(None) == FONT_SCALE_DEFAULT


def test_apply_then_reset_is_exact(qapp: QApplication) -> None:
    """Calling apply(1.5) then apply(1.0) must restore the original size
    — not 1.5× of itself. This is the bug the captured base prevents."""
    base = qapp.font().pointSizeF()
    if base <= 0:
        # Skip on Qt styles that use pixel size; the helper falls back to
        # an arbitrary 10pt base, which makes the assertion brittle.
        pytest.skip("font reports pixel size, can't compare to base")

    apply_font_scale(qapp, 1.5)
    bigger = qapp.font().pointSizeF()
    assert bigger == pytest.approx(base * 1.5, rel=1e-3)

    apply_font_scale(qapp, 1.0)
    restored = qapp.font().pointSizeF()
    assert restored == pytest.approx(base, rel=1e-3)


def test_apply_clamps_out_of_range(qapp: QApplication) -> None:
    applied = apply_font_scale(qapp, 5.0)
    assert applied == FONT_SCALE_MAX
    applied = apply_font_scale(qapp, 0.1)
    assert applied == FONT_SCALE_MIN
