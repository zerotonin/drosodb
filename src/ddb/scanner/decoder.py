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


def detect_qr_region(frame_bgr: np.ndarray) -> tuple[int, int, int, int] | None:
    """Locate the QR bounding box without decoding it.

    Uses OpenCV's finder-pattern detector, which works even when the
    modules are too blurry to decode. Returns (x, y, w, h) in pixel
    coords, or None if no QR is found.
    """
    ok, points = _OPENCV_DETECTOR.detect(frame_bgr)
    if not ok or points is None or len(points) == 0:
        return None
    pts = points.reshape(-1, 2)
    x_min, y_min = pts.min(axis=0)
    x_max, y_max = pts.max(axis=0)
    h, w = frame_bgr.shape[:2]
    # Pad 15% of QR size so zxing has quiet-zone around the crop.
    pad_x = int((x_max - x_min) * 0.15)
    pad_y = int((y_max - y_min) * 0.15)
    x1 = max(0, int(x_min) - pad_x)
    y1 = max(0, int(y_min) - pad_y)
    x2 = min(w, int(x_max) + pad_x)
    y2 = min(h, int(y_max) + pad_y)
    if x2 <= x1 or y2 <= y1:
        return None
    return x1, y1, x2 - x1, y2 - y1


def decode_image_smart(frame_bgr: np.ndarray) -> list[str]:
    """Decode with a detect→crop→upscale fallback for small / borderline QRs.

    Path:
      1. Plain decode (zxing or OpenCV) on the full frame. Fast, usually hits.
      2. If (1) is empty but the OpenCV finder-pattern detector locates a QR,
         crop to that bounding box, upscale 2×, and decode again.

    Step 2 is what phone scanner apps get for free via region-of-interest
    autofocus. Our fixed-focus webcam can't zoom the optics, so we zoom
    the pixels after the fact — this converts "QR in frame but too small /
    slightly blurred" into a decodable payload in practice.
    """
    hits = decode_image(frame_bgr)
    if hits:
        return hits
    region = detect_qr_region(frame_bgr)
    if region is None:
        return []
    x, y, w, h = region
    crop = frame_bgr[y : y + h, x : x + w]
    if crop.size == 0:
        return []
    big = cv2.resize(crop, (w * 2, h * 2), interpolation=cv2.INTER_CUBIC)
    return decode_image(big)


def frame_sharpness(frame_bgr: np.ndarray, *, roi_only: bool = True) -> float:
    """Variance of the Laplacian — classic blur metric.

    When `roi_only` is True and OpenCV can detect a QR's finder patterns,
    sharpness is measured on just the QR's bounding box (+padding). That's
    far more actionable than whole-frame sharpness, which can read high
    when the user's face is sharp but the tiny QR is mush.

    Falls back to whole-frame if no QR is detected.

    Rule of thumb for our pipeline: >300 decodes reliably, 100–300 is
    borderline, <100 is unrecoverable.
    """
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    if roi_only:
        region = detect_qr_region(frame_bgr)
        if region is not None:
            x, y, w, h = region
            patch = gray[y : y + h, x : x + w]
            if patch.size > 0:
                return float(cv2.Laplacian(patch, cv2.CV_64F).var())
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


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
