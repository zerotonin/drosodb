"""QR payload generator for DDB labels.

Compact payload — just the print code prefixed with `DDB:` — so the QR can
be rendered as a Micro QR on a 17 × 54 mm label (DK-11204). Micro QR uses
up to 17 × 17 modules (M4) vs a full QR's 21 × 21+ (v1+), giving us ~60 %
larger physical modules at the same printed QR size. That's the difference
between "decodable by a soft webcam" and "not".

A `DDB:` prefix is kept so the scanner can discriminate our labels from
random QRs in the camera's field of view.

Legacy `ddb:1:vial:<id>?pc=<pc>&db=<db>` labels already printed in the wild
stay readable — see `ddb.scanner.payload.parse_payload` which accepts both.
"""

from __future__ import annotations

from io import BytesIO

import segno

COMPACT_PREFIX = "DDB:"


def build_payload(print_code: str) -> str:
    """Return the canonical Micro-QR-friendly payload for a print code.

    Example: `build_payload("VWM2D") == "DDB:VWM2D"`.
    """
    return f"{COMPACT_PREFIX}{print_code.upper()}"


def make_qr_png(payload: str, scale: int = 6, border: int = 2) -> bytes:
    """Render `payload` as a PNG. Uses Micro QR when it fits, full QR otherwise.

    Micro QR maxes at ~35 alphanumeric chars (M4 at EC L); our 9-char
    `DDB:VWM2D` comfortably fits M2. If a caller passes a longer string
    (e.g. an older integration) we silently fall back to full QR at EC M
    so the image always renders successfully.
    """
    try:
        qr = segno.make(payload, error="l", micro=True)
    except segno.DataOverflowError:
        qr = segno.make(payload, error="m", micro=False)
    buf = BytesIO()
    qr.save(buf, kind="png", scale=scale, border=border)
    return buf.getvalue()
