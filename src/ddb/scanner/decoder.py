"""Decode QR codes from a BGR image using OpenCV.

A single detector is reused across calls because constructing it is not
free and the scan loop may call `decode_image` hundreds of times per second.
"""

from __future__ import annotations

import cv2
import numpy as np

_DETECTOR = cv2.QRCodeDetector()


def decode_image(frame_bgr: np.ndarray) -> list[str]:
    """Return every QR payload visible in the frame (empty list if none)."""
    ok, decoded, _points, _qrcodes = _DETECTOR.detectAndDecodeMulti(frame_bgr)
    if not ok:
        return []
    return [s for s in decoded if s]


def decode_png_bytes(png_bytes: bytes) -> list[str]:
    """Convenience: decode a PNG in memory (used by tests)."""
    arr = np.frombuffer(png_bytes, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        return []
    return decode_image(img)
