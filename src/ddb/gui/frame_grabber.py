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
from datetime import UTC, datetime
from pathlib import Path

import cv2
import numpy as np
from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QImage

from ddb.camera.capture import open_capture, resolve_role
from ddb.scanner.decoder import decode_image_smart, frame_sharpness


def _bgr_to_qimage(frame: np.ndarray) -> QImage:
    h, w, _ = frame.shape
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    # .copy() because QImage does not own the underlying buffer otherwise.
    return QImage(rgb.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy()


class FrameGrabber(QThread):
    frame_ready = Signal(QImage)
    payload_decoded = Signal(str)
    sharpness_changed = Signal(float)
    auto_snapshot_saved = Signal(str)  # path of the auto-saved frame
    error = Signal(str)

    # Auto-save a snapshot whenever sharpness hits this high-water mark,
    # and keep bumping the mark as we see sharper frames. Helps the user
    # catch the rare "sweet spot" frames without racing the mouse button.
    AUTO_SNAPSHOT_FLOOR = 80.0

    def __init__(
        self,
        role: str,
        *,
        reset_after_s: float = 1.5,
        auto_snapshot_dir: Path | None = None,
    ) -> None:
        super().__init__()
        self.role = role
        self.reset_after_s = reset_after_s
        self.auto_snapshot_dir = auto_snapshot_dir
        self._stop = False
        # Keep a reference to the most recent BGR frame for the debug
        # snapshot feature. Written by this thread, read by the GUI thread;
        # a single attribute load/store is atomic under the GIL, worst-case
        # we see a slightly stale frame — fine for diagnostics.
        self._last_frame: np.ndarray | None = None

    def stop(self) -> None:
        self._stop = True

    def latest_frame(self) -> np.ndarray | None:
        """Return a *copy* of the most recent frame (None if none yet)."""
        frame = self._last_frame
        return frame.copy() if frame is not None else None

    def run(self) -> None:
        try:
            cam = resolve_role(self.role)
        except Exception as e:  # noqa: BLE001 — user-facing error channel
            self.error.emit(str(e))
            return

        last_seen: dict[str, float] = {}
        # Rolling mean of recent sharpness values — keeps the on-screen
        # number from jittering between, say, 180 and 420 between frames.
        sharp_window: list[float] = []
        sharp_window_len = 5
        # High-water mark for auto-snapshot: save any frame whose ROI
        # sharpness beats this. Starts at AUTO_SNAPSHOT_FLOOR; each save
        # bumps the bar so we don't spam the disk with near-identical frames.
        best_sharp = self.AUTO_SNAPSHOT_FLOOR
        last_auto_save = 0.0
        try:
            with open_capture(cam.device_path) as cap:
                while not self._stop:
                    ok, frame = cap.read()
                    if not ok:
                        time.sleep(0.05)
                        continue

                    self._last_frame = frame
                    self.frame_ready.emit(_bgr_to_qimage(frame))

                    # Live sharpness readout — users watch this to tune
                    # label distance without guessing.
                    raw_sharp = frame_sharpness(frame)
                    sharp_window.append(raw_sharp)
                    if len(sharp_window) > sharp_window_len:
                        sharp_window.pop(0)
                    smoothed = sum(sharp_window) / len(sharp_window)
                    self.sharpness_changed.emit(smoothed)

                    now = time.monotonic()
                    # Auto-snapshot any peak — rate-limited so we don't
                    # write 30 PNGs per second if the hand is very steady.
                    if (
                        self.auto_snapshot_dir is not None
                        and raw_sharp > best_sharp
                        and (now - last_auto_save) > 1.5
                    ):
                        path = self._save_auto(frame, raw_sharp)
                        if path is not None:
                            best_sharp = raw_sharp
                            last_auto_save = now
                            self.auto_snapshot_saved.emit(str(path))

                    payloads = decode_image_smart(frame)
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

    def _save_auto(self, frame: np.ndarray, sharpness: float) -> Path | None:
        try:
            d = self.auto_snapshot_dir  # type: ignore[assignment]
            assert d is not None
            d.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            out = d / f"auto_{stamp}_s{int(sharpness)}.png"
            cv2.imwrite(str(out), frame)
            return out
        except OSError:
            return None
