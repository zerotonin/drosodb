"""Raster builder tests — require brother_ql to be installed."""

from __future__ import annotations

from io import BytesIO

import pytest
from PIL import Image

from ddb.printing.raster import PRINTABLE_PX, RasterError, build_raster

pytest.importorskip("brother_ql")


def _png(size: tuple[int, int]) -> bytes:
    im = Image.new("RGB", size, "white")
    buf = BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def test_build_raster_17x54_produces_nonempty() -> None:
    w, h = PRINTABLE_PX["17x54"]
    raster = build_raster(_png((w, h)), label="17x54")
    assert len(raster) > 1024
    assert b"\x1b\x69\x7a" in raster, "ESC i z media-info command missing"


def test_build_raster_auto_resizes_mismatched_png() -> None:
    raster = build_raster(_png((320, 120)), label="17x54")
    assert len(raster) > 1024


def test_build_raster_unknown_label_raises() -> None:
    with pytest.raises(RasterError, match="Unknown label"):
        build_raster(_png((100, 100)), label="99x99")


def test_build_raster_media_info_matches_17x54() -> None:
    w, h = PRINTABLE_PX["17x54"]
    raster = build_raster(_png((w, h)), label="17x54")
    idx = raster.find(b"\x1b\x69\x7a")
    blob = raster[idx : idx + 13]
    # ESC i z  n1 n2 n3 n4 n5 n6 n7 n8 n9 n10
    #   n2 = media type  (0x0b die-cut, 0x0a continuous)
    #   n3 = width mm
    #   n4 = length mm
    assert blob[4] == 0x0B, "should be die-cut"
    assert blob[5] == 17, "media width should be 17mm"
    assert blob[6] == 54, "media length should be 54mm"
