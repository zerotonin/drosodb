"""Parse the QR payload on a DDB label.

Two formats are supported:

  - **Compact** (current): `DDB:<print_code>` — e.g. `DDB:VWM2D`.
    What we generate for every new label. Fits Micro QR.

  - **Legacy — DEPRECATED**: `ddb:1:vial:<vial_id>?pc=<print_code>&db=<database_id>`.
    What earlier DDB versions stamped onto labels. Kept parseable so
    previously-printed labels keep scanning, but nothing in the project
    *writes* this format any more. Once every vial with a legacy label
    has been flipped (giving it a new compact-format label), the legacy
    branch here can be removed.
    TODO: drop legacy parser once flip turnover is complete.

In both cases `print_code` is the primary key we care about; the
scanner's lookup function uses it (or falls back to `vial_id` for legacy
labels whose print code was flipped after printing).
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse


class PayloadParseError(ValueError):
    pass


@dataclass(frozen=True)
class ParsedPayload:
    print_code: str  # always present, upper-cased
    vial_id: int | None = None  # legacy format only
    database_id: str | None = None  # legacy format only

    @property
    def is_compact(self) -> bool:
        return self.vial_id is None and self.database_id is None


def parse_payload(raw: str) -> ParsedPayload:
    """Return a ParsedPayload for either compact or legacy QR content.

    Raises PayloadParseError if the string isn't ours, so callers can
    distinguish "not one of our QRs" from "ours but broken".
    """
    if raw is None:
        raise PayloadParseError("empty payload")
    s = raw.strip()
    if not s:
        raise PayloadParseError("empty payload")
    # Case-insensitive prefix match — compact form is uppercase by spec,
    # but accepting "ddb:" keeps us tolerant of manual entry.
    if not s.lower().startswith("ddb:"):
        raise PayloadParseError(f"not a ddb payload: {raw!r}")

    tail = s[4:]

    # Compact form: "DDB:<print_code>" — no further colons, no query.
    if ":" not in tail and "?" not in tail and "=" not in tail:
        code = tail.strip().upper()
        if not code:
            raise PayloadParseError("compact payload has no print code")
        if not code.isalnum():
            raise PayloadParseError(f"compact payload contains non-alnum chars: {code!r}")
        return ParsedPayload(print_code=code)

    # Legacy form — fall through to URI-style parsing.
    return _parse_legacy(s)


def _parse_legacy(raw: str) -> ParsedPayload:
    parts = urlparse(raw)
    if parts.scheme.lower() != "ddb":
        raise PayloadParseError(f"expected scheme 'ddb', got {parts.scheme!r}")

    path_parts = parts.path.split(":")
    if len(path_parts) != 3:
        raise PayloadParseError(f"malformed path {parts.path!r}")
    _version_s, entity, entity_id_s = path_parts
    try:
        entity_id = int(entity_id_s)
    except ValueError as e:
        raise PayloadParseError(f"non-integer id in {parts.path!r}") from e

    if entity != "vial":
        raise PayloadParseError(f"unsupported entity {entity!r}")

    q = parse_qs(parts.query)
    try:
        print_code = q["pc"][0]
        database_id = q["db"][0]
    except (KeyError, IndexError) as e:
        raise PayloadParseError(f"missing pc/db in query {parts.query!r}") from e

    return ParsedPayload(
        print_code=print_code.upper(),
        vial_id=entity_id,
        database_id=database_id,
    )
