"""Service tests — printer plumbing with a fake backend (no hardware)."""

from __future__ import annotations

from dataclasses import dataclass, field
from io import BytesIO

import pytest
from PIL import Image

from ddb.config import settings
from ddb.printing.service import PrinterError, print_png
from ddb.printing.status import StatusBlock, decode_status_blocks


@dataclass
class FakeBackend:
    """Captures raster payloads and returns canned StatusBlocks."""

    canned: list[StatusBlock] = field(default_factory=list)
    sent: list[bytes] = field(default_factory=list)
    description: str = "fake:backend"

    def send(self, raster: bytes, *, timeout: float = 30.0) -> list[StatusBlock]:  # noqa: ARG002
        self.sent.append(raster)
        return list(self.canned)


def _png_bytes(size: tuple[int, int] = (566, 165)) -> bytes:
    im = Image.new("RGB", size, "white")
    buf = BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def _ok_blocks() -> list[StatusBlock]:
    idle = bytes(
        [0x80, 0x20, 0x42, 0x34, 0x41, 0x30, 0, 0, 0, 0, 0x11, 0x0B,
         0, 0, 0, 0, 0, 0x36, 0x01, 0x01, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
    )
    return decode_status_blocks(idle)


def test_print_png_requires_enabled_printer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "printer_enabled", False)
    with pytest.raises(PrinterError, match="disabled"):
        print_png(_png_bytes())


def test_print_png_with_explicit_backend_bypasses_enabled_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "printer_enabled", False)
    be = FakeBackend(canned=_ok_blocks())
    result = print_png(_png_bytes(), backend=be)
    assert be.sent, "backend never received raster bytes"
    assert result.raster_bytes == len(be.sent[0])
    assert result.last is not None and result.last.is_done


def test_print_png_raises_on_error_status() -> None:
    # err2=0x40 -> media-mismatch-replace
    err = bytes(
        [0x80, 0x20, 0x42, 0x34, 0x41, 0x30, 0, 0, 0, 0x40, 0x11, 0x0B,
         0, 0, 0, 0, 0, 0x36, 0x02, 0x01, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
    )
    be = FakeBackend(canned=decode_status_blocks(err))
    with pytest.raises(PrinterError, match="media-mismatch"):
        print_png(_png_bytes(), backend=be)


def test_print_result_summary_includes_backend_and_bytes() -> None:
    be = FakeBackend(canned=_ok_blocks(), description="fake:unit-test")
    result = print_png(_png_bytes(), backend=be)
    s = result.summary()
    assert "fake:unit-test" in s
    assert str(result.raster_bytes) in s
