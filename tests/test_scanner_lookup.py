from pathlib import Path

import pytest
from sqlmodel import Session, select

from ddb.config import settings
from ddb.models import Genotype
from ddb.qr import build_payload
from ddb.scanner import lookup_by_payload, parse_payload
from ddb.scanner.payload import ParsedPayload
from ddb.workflows import create_vial


@pytest.fixture(autouse=True)
def _labels_in_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "data_dir", tmp_path)


def _make_vial(session: Session):
    session.add(Genotype(name="Canton-S"))
    session.commit()
    geno = session.exec(select(Genotype).where(Genotype.name == "Canton-S")).one()
    return create_vial(session, genotype_id=geno.id).vial


# --- Compact payloads (what we generate today) ---------------------------


def test_lookup_happy_path_compact(session: Session) -> None:
    vial = _make_vial(session)
    raw = build_payload(vial.print_code)
    result = lookup_by_payload(session, parse_payload(raw))
    assert result is not None
    assert result.vial.id == vial.id
    assert result.is_fully_consistent


def test_lookup_missing_vial_returns_none_compact(session: Session) -> None:
    raw = build_payload("NOPE0")
    assert lookup_by_payload(session, parse_payload(raw)) is None


def test_lookup_compact_has_no_database_mismatch(session: Session) -> None:
    """Compact payload carries no db id, so database_matches is always True."""
    vial = _make_vial(session)
    result = lookup_by_payload(session, parse_payload(build_payload(vial.print_code)))
    assert result is not None and result.database_matches


# --- Legacy payloads (labels printed before the Micro-QR switch) ---------


def test_lookup_legacy_happy_path(session: Session) -> None:
    vial = _make_vial(session)
    parsed = ParsedPayload(
        print_code=vial.print_code, vial_id=vial.id, database_id=settings.database_id
    )
    result = lookup_by_payload(session, parsed)
    assert result is not None
    assert result.vial.id == vial.id
    assert result.is_fully_consistent


def test_lookup_legacy_flags_stale_print_code(session: Session) -> None:
    """Label says code XXXXX, vial_id → real vial with a different code."""
    vial = _make_vial(session)
    parsed = ParsedPayload(
        print_code="XXXXX", vial_id=vial.id, database_id=settings.database_id
    )
    result = lookup_by_payload(session, parsed)
    assert result is not None
    assert not result.print_code_matches
    assert result.database_matches
    assert not result.is_fully_consistent


def test_lookup_legacy_flags_foreign_database(session: Session) -> None:
    vial = _make_vial(session)
    parsed = ParsedPayload(
        print_code=vial.print_code, vial_id=vial.id, database_id="other-lab"
    )
    result = lookup_by_payload(session, parsed)
    assert result is not None
    assert result.print_code_matches
    assert not result.database_matches
