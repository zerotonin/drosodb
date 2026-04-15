"""A QLabel that displays camera frames, aspect-correct."""

from __future__ import annotations

from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QLabel, QSizePolicy


class CameraWidget(QLabel):
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
