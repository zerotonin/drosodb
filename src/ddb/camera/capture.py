"""OpenCV capture helpers. The CLI uses these; a future GUI may not.

Kept deliberately small so that swapping OpenCV for Qt's multimedia stack
later touches only this file.
"""

from __future__ import annotations

import time
from contextlib import contextmanager

import cv2

from ddb.camera.config import Role, load_assignments
from ddb.camera.enumeration import CameraInfo, discover_cameras


class CameraNotAssignedError(RuntimeError):
    pass


class CameraNotFoundError(RuntimeError):
    pass


def resolve_role(role: Role) -> CameraInfo:
    """Look up the current device path for a role ('front' | 'back')."""
    assignments = load_assignments()
    bus_path = assignments.bus_path_for(role)
    if bus_path is None:
        raise CameraNotAssignedError(
            f"No camera assigned to role {role!r}. Run `ddb camera assign` first."
        )
    for cam in discover_cameras():
        if cam.bus_path == bus_path:
            return cam
    raise CameraNotFoundError(
        f"Camera at bus path {bus_path!r} (role {role!r}) not currently connected."
    )


@contextmanager
def open_capture(device_path: str, width: int = 1920, height: int = 1080):
    """Open a VideoCapture and request the highest sensible resolution.

    QR decoding needs as many pixels per module as possible — more so on
    soft webcams. We ask for 1920x1080; the driver silently degrades to
    whatever it actually supports (e.g. 1600x1200, 1280x720), so this is
    a no-regression bump.

    Also ensures autofocus + auto-exposure are enabled. Cheap UVC cameras
    sometimes ship with them off or don't implement them, in which case
    `set()` is a harmless no-op.
    """
    cap = cv2.VideoCapture(device_path, cv2.CAP_V4L2)
    if not cap.isOpened():
        raise CameraNotFoundError(f"Could not open {device_path}")
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    cap.set(cv2.CAP_PROP_AUTOFOCUS, 1)
    cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 3)
    try:
        yield cap
    finally:
        cap.release()


def preview(device_path: str, window_title: str, duration_s: float | None = None) -> None:
    """Open a live preview window. Press 'q' or close the window to exit.

    If `duration_s` is set, the window auto-closes after that many seconds —
    handy for the `assign` workflow where we show each camera briefly.
    """
    with open_capture(device_path) as cap:
        cv2.namedWindow(window_title, cv2.WINDOW_NORMAL)
        start = time.monotonic()
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            cv2.imshow(window_title, frame)
            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                break
            if duration_s is not None and time.monotonic() - start >= duration_s:
                break
            if cv2.getWindowProperty(window_title, cv2.WND_PROP_VISIBLE) < 1:
                break
        cv2.destroyWindow(window_title)
