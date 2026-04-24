"""Resolve a parsed QR payload to a Vial row in the database.

For compact labels we only have the print_code; lookup is by print_code
and we prefer the active row (there is at most one active row per code,
enforced by the partial unique index).

For legacy labels we also have vial_id and database_id. If the print_code
no longer matches an active vial (because it was flipped), we fall back
to the vial_id so old printed labels still resolve to SOMETHING — just
with `print_code_matches=False` so the caller can warn about a stale label.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlmodel import Session, select

from ddb.config import settings
from ddb.models import Vial
from ddb.reports import VialDetail, vial_detail
from ddb.scanner.payload import ParsedPayload


@dataclass
class LookupResult:
    vial: Vial
    # False if what's printed on the label no longer matches what the DB
    # holds for this vial — typical cause: the vial was flipped after the
    # label was printed. UI should warn in that case.
    print_code_matches: bool
    database_matches: bool

    @property
    def is_fully_consistent(self) -> bool:
        return self.print_code_matches and self.database_matches


def lookup_by_payload(session: Session, parsed: ParsedPayload) -> LookupResult | None:
    # First try an active vial with that print_code — this is the common
    # hot path for both compact and legacy labels.
    vial = session.exec(
        select(Vial).where(
            Vial.print_code == parsed.print_code,
            Vial.is_active.is_(True),
        )
    ).first()

    # Fallback: if the label's code no longer appears as active (e.g. the
    # vial was flipped and that code was reused later), try any vial with
    # that code; and if the payload carried a vial_id (legacy), try that.
    if vial is None:
        vial = session.exec(select(Vial).where(Vial.print_code == parsed.print_code)).first()
    if vial is None and parsed.vial_id is not None:
        vial = session.get(Vial, parsed.vial_id)
    if vial is None:
        return None

    # Database consistency: legacy labels carry a db id we can compare
    # against; compact labels don't — they're always "right" by construction.
    if parsed.database_id is None:
        database_matches = True
    else:
        database_matches = parsed.database_id == settings.database_id

    return LookupResult(
        vial=vial,
        print_code_matches=(vial.print_code == parsed.print_code),
        database_matches=database_matches,
    )


def lookup_detail_by_payload(session: Session, parsed: ParsedPayload) -> VialDetail | None:
    """One-call bridge from a parsed payload to a fully-populated VialDetail.

    The Scan tab's preview thread and manual-entry handler both want
    the same thing: "given a payload, give me everything needed to
    paint the detail panel, or None if the payload isn't known here."
    Compose `lookup_by_payload` + `vial_detail` so neither call site
    duplicates the select / fallback logic.
    """
    found = lookup_by_payload(session, parsed)
    if found is None:
        return None
    return vial_detail(session, found.vial.id)
