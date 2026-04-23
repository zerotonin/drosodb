"""Build a Brother raster byte stream from a PNG label.

Wraps `brother_ql.conversion.convert` and normalises PNG dimensions to the
printable area the library insists on (which excludes the 1–1.5 mm unprintable
margins on each side of the tape). We keep our own `render_label` output at
the raw media dimensions so it looks right on screen; resizing happens here
only when converting for the printer.
"""

from __future__ import annotations

from io import BytesIO

from PIL import Image

# brother_ql's printable-area spec for each DK label identifier, (w, h) px at
# 300 DPI. Values come from `brother_ql.labels.LabelsManager.dots_printable`.
# We hardcode the ones DDB actually uses so users don't need to import the
# library just to know the target size.
PRINTABLE_PX: dict[str, tuple[int, int]] = {
    "17x54": (566, 165),    # DK-11204 die-cut (our default)
    "29x90": (991, 306),    # DK-11201 address label
    "38x90": (991, 413),    # DK-11208 shipping label
    "12": (106, 0),         # DK-N55224 continuous 12mm (height = any)
    "29": (306, 0),         # DK-22210 continuous 29mm
    "62": (696, 0),         # DK-22205 continuous 62mm
}


class RasterError(RuntimeError):
    """Raised when raster conversion fails (usually: unsupported label id)."""


def build_raster(
    png_bytes: bytes,
    *,
    model: str = "QL-820NWB",
    label: str = "17x54",
    cut: bool = True,
    hq: bool = True,
    threshold: float = 70.0,
    compress: bool = True,
) -> bytes:
    """Convert a PNG into a raster byte stream the printer can consume.

    The returned bytes can be written directly to:
      - A TCP socket on the printer's port 9100 (ethernet/wifi).
      - An RFCOMM socket on channel 1 (Bluetooth SPP).
      - A file, then USB-forwarded via `cat > /dev/usb/lp0`.
    """
    try:
        # brother_ql must be an optional import — the package is only needed
        # when actually printing; devs running tests without hardware bits
        # should still be able to `import ddb.printing` for the decoder.
        from brother_ql.conversion import convert
        from brother_ql.raster import BrotherQLRaster
    except ImportError as e:  # pragma: no cover — exercised in manual runs
        raise RasterError(
            "brother_ql is not installed. Install with: pip install brother_ql"
        ) from e

    im = Image.open(BytesIO(png_bytes)).convert("RGB")

    target = PRINTABLE_PX.get(label)
    if target is None:
        raise RasterError(
            f"Unknown label identifier {label!r}. "
            f"Known: {sorted(PRINTABLE_PX)}. Pass a new entry in PRINTABLE_PX."
        )
    # target is (w, h) landscape. height==0 means "any length allowed" for
    # continuous tapes — skip resize in that case.
    if target[1] and im.size != target:
        im = im.resize(target, Image.LANCZOS)

    qlr = BrotherQLRaster(model)
    qlr.exception_on_warning = True
    return convert(
        qlr=qlr,
        images=[im],
        label=label,
        rotate="auto",
        threshold=threshold,
        dither=False,
        compress=compress,
        hq=hq,
        cut=cut,
    )
