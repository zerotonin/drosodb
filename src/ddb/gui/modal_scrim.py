# ╔══════════════════════════════════════════════════════════════════╗
# ║  DDB — gui.modal_scrim                                           ║
# ║  « dim the window behind every modal dialog »                    ║
# ╠══════════════════════════════════════════════════════════════════╣
# ║  Qt's own Wayland frames are flat grey with no shadow, so a      ║
# ║  dialog blends into the identical window behind it. A            ║
# ║  translucent backdrop over the dialog's parent window (the       ║
# ║  Material / Fluent / web pattern) makes the active dialog        ║
# ║  unmistakable on any compositor.                                 ║
# ╚══════════════════════════════════════════════════════════════════╝
"""App-wide modal backdrop.

Install once per application with :class:`ModalScrim`. Every modal
``QDialog`` (including ``QMessageBox`` and dialogs opened from other
dialogs) dims the window it belongs to while it is shown. Nothing needs to
change in the dialogs themselves.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QPropertyAnimation, Qt
from PySide6.QtGui import QColor, QPainter, QPaintEvent
from PySide6.QtWidgets import QApplication, QDialog, QGraphicsOpacityEffect, QWidget

from ddb.gui.theme import SCRIM_ALPHA, SCRIM_FADE_MS, SCRIM_RGB


class _Backdrop(QWidget):
    """Translucent overlay covering one host window; ignores the mouse."""

    def __init__(self, host: QWidget) -> None:
        super().__init__(host)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._effect = QGraphicsOpacityEffect(self)
        self._effect.setOpacity(0.0)
        self.setGraphicsEffect(self._effect)
        self._fade = QPropertyAnimation(self._effect, b"opacity", self)
        self._fade.setDuration(SCRIM_FADE_MS)
        self._fade.finished.connect(self._hide_if_faded_out)
        self.hide()

    def fade_in(self) -> None:
        self.setGeometry(self.parentWidget().rect())
        self.raise_()
        self.show()
        self._animate_to(1.0)

    def fade_out(self) -> None:
        self._animate_to(0.0)

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        QPainter(self).fillRect(self.rect(), QColor(*SCRIM_RGB, SCRIM_ALPHA))

    def _animate_to(self, target: float) -> None:
        self._fade.stop()
        self._fade.setStartValue(self._effect.opacity())
        self._fade.setEndValue(target)
        self._fade.start()

    def _hide_if_faded_out(self) -> None:
        if self._fade.endValue() == 0.0:
            self.hide()


class ModalScrim(QObject):
    """Event filter that shows a backdrop behind each visible modal dialog.

    Keys are ``id()`` of the Qt objects, released on ``destroyed``, so a
    dialog deleted without a Hide event can't leave its host dimmed.
    """

    def __init__(self, app: QApplication) -> None:
        super().__init__(app)
        self._backdrops: dict[int, _Backdrop] = {}
        self._dimmed_by: dict[int, int] = {}  # dialog id → host id
        app.installEventFilter(self)

    def is_dimmed(self, host: QWidget) -> bool:
        return id(host) in self._dimmed_by.values()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        kind = event.type()
        if kind == QEvent.Type.Show:
            if isinstance(watched, QDialog) and watched.isWindow() and watched.isModal():
                self._dim_behind(watched)
        elif kind == QEvent.Type.Hide:
            self._release(id(watched))
        elif kind == QEvent.Type.Resize:
            backdrop = self._backdrops.get(id(watched))
            if backdrop is not None and isinstance(watched, QWidget):
                backdrop.setGeometry(watched.rect())
        return False

    def _dim_behind(self, dialog: QDialog) -> None:
        host = _host_window(dialog)
        if host is None or id(dialog) in self._dimmed_by:
            return
        host_key = id(host)
        backdrop = self._backdrops.get(host_key)
        if backdrop is None:
            backdrop = _Backdrop(host)
            self._backdrops[host_key] = backdrop
            host.destroyed.connect(lambda _=None, k=host_key: self._forget_host(k))
        dialog_key = id(dialog)
        self._dimmed_by[dialog_key] = host_key
        dialog.destroyed.connect(lambda _=None, k=dialog_key: self._release(k))
        backdrop.fade_in()

    def _release(self, dialog_key: int) -> None:
        host_key = self._dimmed_by.pop(dialog_key, None)
        if host_key is None or host_key in self._dimmed_by.values():
            return
        backdrop = self._backdrops.get(host_key)
        if backdrop is not None:
            backdrop.fade_out()

    def _forget_host(self, host_key: int) -> None:
        self._backdrops.pop(host_key, None)
        for dialog_key in [d for d, h in self._dimmed_by.items() if h == host_key]:
            del self._dimmed_by[dialog_key]


def _host_window(dialog: QDialog) -> QWidget | None:
    """The window a dialog belongs to: its parent's window, else the active one."""
    parent = dialog.parentWidget()
    host = parent.window() if parent is not None else QApplication.activeWindow()
    if host is None or host is dialog or not host.isVisible():
        return None
    return host
