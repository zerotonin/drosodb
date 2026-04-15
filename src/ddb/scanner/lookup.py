"""Resolve a parsed payload to a Vial in the database."""

from __future__ import annotations

from dataclasses import dataclass

from sqlmodel import Session

from ddb.config import settings
from ddb.models import Vial
from ddb.scanner.payload import ParsedPayload


@dataclass
class LookupResult:
    vial: Vial
    # True when the scanned payload's pc= and db= match the vial's current
    # state. A mismatch usually means a label was re-printed or the vial
    # was flipped, i.e. the physical label is stale.
    print_code_matches: bool
    database_matches: bool

    @property
    def is_fully_consistent(self) -> bool:
        return self.print_code_matches and self.database_matches


def lookup_by_payload(session: Session, parsed: ParsedPayload) -> LookupResult | None:
    if parsed.entity != "vial":
        return None
    vial = session.get(Vial, parsed.entity_id)
    if vial is None:
        return None
    return LookupResult(
        vial=vial,
        print_code_matches=(vial.print_code == parsed.print_code),
        database_matches=(parsed.database_id == settings.database_id),
    )
