"""A QLabel that displays camera frames, aspect-correct, with a QR-guide overlay.

The overlay is four L-shaped corner brackets marking roughly where to hold
the label — same visual pattern as the iOS / Android camera QR finders.
It's subtle enough not to distract, and geometrically anchored to the
*displayed* pixmap rect (not the widget rect) so black letterboxing bars
don't get brackets painted on them.
"""

from __future__ import annotations

from PySide6.QtCore import QRect, Qt, Slot
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QLabel, QSizePolicy


class CameraWidget(QLabel):
    # Guide-square size as a fraction of the shorter pixmap dimension.
    GUIDE_FRACTION = 0.50
    # Length of each corner bracket as a fraction of the guide-square side.
    BRACKET_FRACTION = 0.18
    BRACKET_COLOR = QColor(80, 220, 120, 210)  # soft green, slightly transparent
    BRACKET_WIDTH = 4

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet("background-color: #111; color: #ccc;")
        self.setMinimumSize(480, 270)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setText("(no camera feed)")
        self._last_image: QImage | None = None

    @Slot(QImage)
    def on_frame(self, image: QImage) -> None:
        self._last_image = image
        self._repaint()

    def _repaint(self) -> None:
        if self._last_image is None:
            return
        pixmap = QPixmap.fromImage(self._last_image)
        self.setPixmap(
            pixmap.scaled(
                self.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._repaint()

    # ------------------------------------------------------------------
    # QR-guide overlay
    # ------------------------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802
        # Let QLabel paint the pixmap (or placeholder text) first.
        super().paintEvent(event)

        if self.pixmap().isNull():
            return

        rect = self._pixmap_display_rect()
        if rect.isEmpty():
            return

        side = int(min(rect.width(), rect.height()) * self.GUIDE_FRACTION)
        cx, cy = rect.center().x(), rect.center().y()
        guide = QRect(cx - side // 2, cy - side // 2, side, side)
        bracket_len = max(12, int(side * self.BRACKET_FRACTION))

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(self.BRACKET_COLOR, self.BRACKET_WIDTH, Qt.PenStyle.SolidLine)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)

        x1, y1, x2, y2 = guide.left(), guide.top(), guide.right(), guide.bottom()
        # Four L-brackets, one per corner.
        painter.drawLine(x1, y1, x1 + bracket_len, y1)
        painter.drawLine(x1, y1, x1, y1 + bracket_len)
        painter.drawLine(x2, y1, x2 - bracket_len, y1)
        painter.drawLine(x2, y1, x2, y1 + bracket_len)
        painter.drawLine(x1, y2, x1 + bracket_len, y2)
        painter.drawLine(x1, y2, x1, y2 - bracket_len)
        painter.drawLine(x2, y2, x2 - bracket_len, y2)
        painter.drawLine(x2, y2, x2, y2 - bracket_len)
        painter.end()

    def _pixmap_display_rect(self) -> QRect:
        """Rect occupied by the scaled pixmap inside this label (letterbox-aware)."""
        pm = self.pixmap()
        if pm.isNull():
            return QRect()
        # self.pixmap() is the already-scaled pixmap we last set. It was
        # scaled with KeepAspectRatio, so we can just centre it in `self`.
        pw, ph = pm.width(), pm.height()
        x = (self.width() - pw) // 2
        y = (self.height() - ph) // 2
        return QRect(x, y, pw, ph)
