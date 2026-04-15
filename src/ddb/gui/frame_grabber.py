"""Camera-frame producer thread.

Owns the OpenCV capture and runs the QR decoder on each frame. Emits:

  - `frame_ready(QImage)`   — for widgets to paint the live preview
  - `payload_decoded(str)`  — for handlers to react to a scanned QR

Both emissions happen from a QThread worker loop; consumers connect with
the default auto-connection so their slots run on the main (GUI) thread.

Design note: we keep capture + decode in ONE thread so there's a single
writer to the video device. A separate decoder thread would need a frame
queue and would add latency that's not needed here.
"""

from __future__ import annotations

import time

import cv2
import numpy as np
from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QImage

from ddb.camera.capture import open_capture, resolve_role
from ddb.scanner.decoder import decode_image


def _bgr_to_qimage(frame: np.ndarray) -> QImage:
    h, w, _ = frame.shape
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    # .copy() because QImage does not own the underlying buffer otherwise.
    return QImage(rgb.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy()


class FrameGrabber(QThread):
    frame_ready = Signal(QImage)
    payload_decoded = Signal(str)
    error = Signal(str)

    def __init__(self, role: str, *, reset_after_s: float = 1.5) -> None:
        super().__init__()
        self.role = role
        self.reset_after_s = reset_after_s
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        try:
            cam = resolve_role(self.role)
        except Exception as e:  # noqa: BLE001 — user-facing error channel
            self.error.emit(str(e))
            return

        last_seen: dict[str, float] = {}
        try:
            with open_capture(cam.device_path) as cap:
                while not self._stop:
                    ok, frame = cap.read()
                    if not ok:
                        time.sleep(0.05)
                        continue

                    self.frame_ready.emit(_bgr_to_qimage(frame))

                    now = time.monotonic()
                    payloads = decode_image(frame)
                    for p in payloads:
                        prev = last_seen.get(p)
                        if prev is None or (now - prev) > self.reset_after_s:
                            self.payload_decoded.emit(p)
                        last_seen[p] = now
                    # Drop stale entries so memory is bounded.
                    last_seen = {
                        k: t for k, t in last_seen.items() if now - t <= self.reset_after_s
                    }
        except Exception as e:  # noqa: BLE001
            self.error.emit(str(e))
