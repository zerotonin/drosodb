"""Decode QR codes from a BGR image.

Two backends, tried in order:
  1. `zxing-cpp` — better recovery on small/angled/real-world printed labels
     (our 17mm die-cut puts the QR at ~13mm, which OpenCV often misses).
  2. `cv2.QRCodeDetector` — fallback for environments without zxing-cpp.

`decode_image(frame_bgr)` returns every payload found in the frame; the
scan loop treats the list as a set and debounces by payload string, so
multiple codes per frame Just Work if zxing-cpp is present.
"""

from __future__ import annotations

import cv2
import numpy as np

try:
    import zxingcpp

    _ZXING_AVAILABLE = True
except ImportError:  # pragma: no cover — exercised in minimal environments
    zxingcpp = None  # type: ignore[assignment]
    _ZXING_AVAILABLE = False

_OPENCV_DETECTOR = cv2.QRCodeDetector()


def _decode_zxing(frame_bgr: np.ndarray) -> list[str]:
    """zxing-cpp path. Accepts BGR; zxing handles the colour conversion."""
    results = zxingcpp.read_barcodes(
        frame_bgr,
        formats=[
            zxingcpp.BarcodeFormat.QRCode,
            zxingcpp.BarcodeFormat.MicroQRCode,
        ],
    )
    return [r.text for r in results if r.text]


def _decode_opencv(frame_bgr: np.ndarray) -> list[str]:
    ok, decoded, _points, _qrcodes = _OPENCV_DETECTOR.detectAndDecodeMulti(frame_bgr)
    if not ok:
        return []
    return [s for s in decoded if s]


def decode_image(frame_bgr: np.ndarray) -> list[str]:
    """Return every QR payload visible in the frame (empty list if none).

    Tries zxing-cpp first; falls back to OpenCV if zxing is unavailable OR
    finds nothing. The fallback handles the edge case where OpenCV happens
    to recover a particular frame that zxing misses (rare but not zero).
    """
    payloads: list[str] = []
    if _ZXING_AVAILABLE:
        payloads = _decode_zxing(frame_bgr)
    if not payloads:
        payloads = _decode_opencv(frame_bgr)
    return payloads


def decode_png_bytes(png_bytes: bytes) -> list[str]:
    """Convenience: decode a PNG in memory (used by tests)."""
    arr = np.frombuffer(png_bytes, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        return []
    return decode_image(img)


def active_backend() -> str:
    """Report which decoder backend is live (useful for `ddb scan` logs)."""
    return "zxing-cpp" if _ZXING_AVAILABLE else "opencv"
