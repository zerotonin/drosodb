"""Parse the ddb:1:vial:... URI that we stamp into every QR code."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse


class PayloadParseError(ValueError):
    pass


@dataclass(frozen=True)
class ParsedPayload:
    version: int
    entity: str  # currently always "vial"
    entity_id: int
    print_code: str
    database_id: str


def parse_payload(raw: str) -> ParsedPayload:
    """Accept "ddb:1:vial:42?pc=AB12Z&db=local", return a ParsedPayload.

    Raises PayloadParseError on any shape mismatch so callers can
    distinguish "not one of ours" from "ours but broken".
    """
    if not raw or not raw.startswith("ddb:"):
        raise PayloadParseError(f"not a ddb payload: {raw!r}")

    parts = urlparse(raw)
    if parts.scheme != "ddb":
        raise PayloadParseError(f"expected scheme 'ddb', got {parts.scheme!r}")

    path_parts = parts.path.split(":")
    if len(path_parts) != 3:
        raise PayloadParseError(f"malformed path {parts.path!r}")
    version_s, entity, entity_id_s = path_parts
    try:
        version = int(version_s)
        entity_id = int(entity_id_s)
    except ValueError as e:
        raise PayloadParseError(f"non-integer field in path {parts.path!r}") from e

    if entity != "vial":
        raise PayloadParseError(f"unsupported entity {entity!r}")

    q = parse_qs(parts.query)
    try:
        print_code = q["pc"][0]
        database_id = q["db"][0]
    except (KeyError, IndexError) as e:
        raise PayloadParseError(f"missing pc/db in query {parts.query!r}") from e

    return ParsedPayload(
        version=version,
        entity=entity,
        entity_id=entity_id,
        print_code=print_code.upper(),
        database_id=database_id,
    )
