"""Screen-aware sizing shared by the main window and dialogs."""

from __future__ import annotations

from PySide6.QtGui import QGuiApplication


def clamp_to_screen(width: int, height: int, fraction: float = 0.95) -> tuple[int, int]:
    """Shrink a preferred size to fit ``fraction`` of the primary screen."""
    screen = QGuiApplication.primaryScreen()
    if screen is None:
        return width, height
    avail = screen.availableGeometry()
    return (
        min(width, int(avail.width() * fraction)),
        min(height, int(avail.height() * fraction)),
    )
