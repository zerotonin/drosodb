"""Application-wide font scaling for the DDB GUI.

The setting lives in `Settings.gui_font_scale` (persisted via `.env`).
On startup `apply_font_scale(app, settings.gui_font_scale)` rescales the
QApplication default font; Settings-tab and Ctrl+= / Ctrl+- shortcuts
call back into the same helper to retune live.

The base point size is captured on the first call so subsequent calls
always scale from the unscaled system default — calling
`apply_font_scale(app, 1.5)` followed by `apply_font_scale(app, 1.0)`
restores the original size exactly, rather than drifting.
"""

from __future__ import annotations

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

FONT_SCALE_MIN = 0.7
FONT_SCALE_MAX = 2.0
FONT_SCALE_STEP = 0.1
FONT_SCALE_DEFAULT = 1.0

# The unscaled system default — captured on the first call so we can
# restore exactly, not approximately. None until then.
_BASE_POINT_SIZE: float | None = None


def clamp_font_scale(scale: float) -> float:
    """Constrain to the supported range; bad input → default."""
    try:
        s = float(scale)
    except (TypeError, ValueError):
        return FONT_SCALE_DEFAULT
    return max(FONT_SCALE_MIN, min(FONT_SCALE_MAX, s))


def apply_font_scale(app: QApplication, scale: float) -> float:
    """Set the application-wide font to `base_size * scale`.

    Returns the actual (clamped) scale that was applied so callers can
    keep `settings.gui_font_scale` in sync with reality.
    """
    global _BASE_POINT_SIZE

    s = clamp_font_scale(scale)
    f: QFont = app.font()
    if _BASE_POINT_SIZE is None:
        # First call — pin the system default as the base. Some Qt styles
        # use pixel size instead of point size; fall back gracefully.
        pt = f.pointSizeF()
        if pt <= 0:
            px = f.pixelSize()
            pt = float(px) * 0.75 if px > 0 else 10.0
        _BASE_POINT_SIZE = pt

    f.setPointSizeF(_BASE_POINT_SIZE * s)
    app.setFont(f)
    return s


def reset_base_point_size_for_tests() -> None:
    """Tests construct their own QApplication; this lets them pin a fresh base."""
    global _BASE_POINT_SIZE
    _BASE_POINT_SIZE = None
