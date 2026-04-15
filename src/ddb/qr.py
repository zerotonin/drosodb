"""Build the QR payload for a vial and render a QR image."""

from __future__ import annotations

from io import BytesIO

import segno

PAYLOAD_VERSION = 1


def build_payload(vial_id: int, print_code: str, database_id: str) -> str:
    """Canonical scanner-side URI for a vial.

    Format matches the spec:
        ddb:1:vial:<vial_id>?pc=<PRINT>&db=<DBID>
    """
    return f"ddb:{PAYLOAD_VERSION}:vial:{vial_id}?pc={print_code}&db={database_id}"


def make_qr_png(payload: str, scale: int = 6, border: int = 2) -> bytes:
    """Render a QR (not micro-QR) PNG for the given payload.

    We use regular QR rather than micro-QR because scanner support is more
    universal; at scale=6 on a 17mm label it still fits comfortably. Error
    correction 'M' balances size against a scratched / smudged label.
    """
    qr = segno.make(payload, error="m", micro=False)
    buf = BytesIO()
    qr.save(buf, kind="png", scale=scale, border=border)
    return buf.getvalue()
