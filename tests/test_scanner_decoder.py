"""Render a QR with our own generator, decode it with our own detector.

End-to-end sanity check that our chosen QR parameters (error correction
level, scale, border) produce something the runtime decoder can actually
read back. If this fails, field labels probably won't scan either.

Prefers the zxing-cpp backend when installed; falls back to OpenCV.
"""

from ddb.qr import build_payload, make_qr_png
from ddb.scanner.decoder import active_backend, decode_png_bytes


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


def test_full_label_png_decodes_same_as_naked_qr() -> None:
    """The QR embedded in a full rendered 17x54mm label must still decode.

    This protects against a future change to label layout (smaller QR, added
    borders, resampling mode) accidentally breaking scannability.
    """
    from ddb.labels import render_label

    raw = build_payload(vial_id=42, print_code="VWM2D", database_id="local")
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
        f"label QR did not decode back to its payload using {active_backend()!r}; "
        f"got {decoded!r}"
    )
