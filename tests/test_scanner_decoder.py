"""Render a QR with our own generator, decode it with our own detector.

End-to-end sanity check that our chosen QR parameters (error correction
level, scale, border) produce something the runtime decoder can actually
read back. If this fails, field labels probably won't scan either.

Prefers the zxing-cpp backend when installed; falls back to OpenCV.
"""

from ddb.qr import build_payload, make_qr_png
from ddb.scanner.decoder import active_backend, decode_png_bytes


def test_decode_roundtrip() -> None:
    raw = build_payload("AB12Z")
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


def test_full_label_png_decodes_same_as_naked_qr() -> None:
    """The QR embedded in a full rendered 17x54mm label must still decode.

    This protects against a future change to label layout (smaller QR, added
    borders, resampling mode) accidentally breaking scannability.
    """
    from ddb.labels import render_label

    raw = build_payload("VWM2D")
    png = render_label(
        vial_id=42,
        print_code="VWM2D",
        genotype_name="spalthof",
        database_id="local",
        genotype_notation="+/+ ; GAL4-Bloop/CyO ; +/+",
        generation=3,
        created_date="2026-04-16",
    )
    decoded = decode_png_bytes(png)
    assert raw in decoded, (
        f"label QR did not decode back to its payload using {active_backend()!r}; got {decoded!r}"
    )


def test_smart_decode_matches_plain_on_clean_input() -> None:
    """The detect→crop→upscale fallback must never regress the happy path."""

    import cv2
    import numpy as np

    from ddb.scanner.decoder import decode_image, decode_image_smart

    raw = build_payload("SMART")
    png = make_qr_png(raw, scale=6, border=2)
    arr = np.frombuffer(png, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    assert raw in decode_image(img)
    assert raw in decode_image_smart(img)


def test_frame_sharpness_uniform_image_is_zero() -> None:
    """A perfectly uniform image has Laplacian variance ≈ 0."""
    import numpy as np

    from ddb.scanner.decoder import frame_sharpness

    flat = np.full((200, 200, 3), 128, dtype=np.uint8)
    assert frame_sharpness(flat) < 1.0
