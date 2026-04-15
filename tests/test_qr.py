from ddb.qr import build_payload, make_qr_png


def test_payload_format() -> None:
    assert build_payload(42, "AB12Z", "local") == "ddb:1:vial:42?pc=AB12Z&db=local"


def test_qr_png_is_valid_png() -> None:
    png = make_qr_png("hello")
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    assert len(png) > 100
