"""Render a QR with our own generator, decode it with our own detector.

This is an end-to-end sanity check that the QR library settings (error
correction level, scale, border) we chose produce an image that OpenCV
can actually read back. If this ever fails, field labels probably won't
scan either.
"""

from ddb.qr import build_payload, make_qr_png
from ddb.scanner.decoder import decode_png_bytes


def test_decode_roundtrip() -> None:
    raw = build_payload(vial_id=7, print_code="AB12Z", database_id="local")
    png = make_qr_png(raw, scale=6, border=2)
    decoded = decode_png_bytes(png)
    assert raw in decoded


def test_empty_image_decodes_to_nothing() -> None:
    # 1x1 white PNG; trivially no QR.
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (1, 1), "white").save(buf, format="PNG")
    assert decode_png_bytes(buf.getvalue()) == []
