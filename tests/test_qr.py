from ddb.qr import COMPACT_PREFIX, build_payload, make_qr_png


def test_payload_is_compact_micro_qr_friendly() -> None:
    assert build_payload("AB12Z") == f"{COMPACT_PREFIX}AB12Z"


def test_payload_lowercases_to_upper() -> None:
    # Print codes are always upper-cased on the wire so the scanner's
    # alphanumeric-QR-mode path stays efficient.
    assert build_payload("ab12z") == f"{COMPACT_PREFIX}AB12Z"


def test_qr_png_is_valid_png() -> None:
    png = make_qr_png("hello")
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    assert len(png) > 100


def test_short_payload_renders_as_micro_qr() -> None:
    """A ~9-char payload should fit Micro QR (≤17 modules per side)."""
    from io import BytesIO

    from PIL import Image

    png = make_qr_png("DDB:VWM2D", scale=6, border=2)
    img = Image.open(BytesIO(png))
    # Micro QR M2 is 13 modules + 2*2 quiet = 17. At scale 6 → 102 px.
    # Full QR v1 is 21 + 2*4 = 29 → 174 px at the same scale.
    # Anything below ~150 px tells us we landed in Micro QR territory.
    assert img.width <= 150, f"expected micro QR (<=150px at scale=6, border=2), got {img.width}"


def test_long_payload_falls_back_to_full_qr() -> None:
    """If the caller passes something too big for Micro QR, we must not crash."""
    long_payload = "DDB:" + ("X" * 40)
    png = make_qr_png(long_payload, scale=4, border=2)
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
