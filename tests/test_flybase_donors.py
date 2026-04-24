"""Tests for `ensure_collection_donor`.

Covers:
  - Known collection → canonical curated Donor (short_name + institution).
  - Idempotent: repeated calls return the same id, don't create duplicates.
  - Unknown collection → fallback Donor with that exact name and no
    curated institution.
"""

from __future__ import annotations

from sqlmodel import Session, select

from ddb.flybase.donors import COLLECTION_DONORS, ensure_collection_donor
from ddb.models import Donor


def test_creates_canonical_bdsc_donor(session: Session) -> None:
    donor = ensure_collection_donor(session, "Bloomington")
    assert donor.name == "BDSC"
    assert donor.institution is not None
    assert "Bloomington" in donor.institution


def test_creates_canonical_vdrc_donor(session: Session) -> None:
    donor = ensure_collection_donor(session, "Vienna")
    assert donor.name == "VDRC"
    assert donor.institution is not None
    assert "Vienna" in donor.institution


def test_is_idempotent_within_a_session(session: Session) -> None:
    a = ensure_collection_donor(session, "Kyoto")
    b = ensure_collection_donor(session, "Kyoto")
    assert a.id == b.id

    # And confirm there's only one row in the DB.
    rows = session.exec(select(Donor).where(Donor.name == "DGGR")).all()
    assert len(rows) == 1


def test_unknown_collection_falls_back_to_its_own_name(session: Session) -> None:
    donor = ensure_collection_donor(session, "BananaStocks")
    assert donor.name == "BananaStocks"
    assert donor.institution is None
    assert donor.contact is None


def test_every_curated_collection_resolves(session: Session) -> None:
    """Catches a typo in COLLECTION_DONORS that would fall through to the
    fallback path silently for what should be a curated entry."""
    for collection_short_name in COLLECTION_DONORS:
        donor = ensure_collection_donor(session, collection_short_name)
        curated = COLLECTION_DONORS[collection_short_name]
        assert donor.name == curated.short_name
        assert donor.institution == curated.institution
