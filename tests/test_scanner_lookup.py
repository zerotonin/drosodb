from pathlib import Path

import pytest
from sqlmodel import Session

from ddb.config import settings
from ddb.models import Genotype
from ddb.qr import build_payload
from ddb.scanner import lookup_by_payload, parse_payload
from ddb.workflows import create_vial


@pytest.fixture(autouse=True)
def _labels_in_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "data_dir", tmp_path)


def _make_vial(session: Session):
    session.add(Genotype(name="Canton-S"))
    session.commit()
    geno = session.exec(
        __import__("sqlmodel").select(Genotype).where(Genotype.name == "Canton-S")
    ).one()
    return create_vial(session, genotype_id=geno.id).vial


def test_lookup_happy_path(session: Session) -> None:
    vial = _make_vial(session)
    raw = build_payload(vial.id, vial.print_code, settings.database_id)
    result = lookup_by_payload(session, parse_payload(raw))
    assert result is not None
    assert result.vial.id == vial.id
    assert result.is_fully_consistent


def test_lookup_missing_vial_returns_none(session: Session) -> None:
    raw = build_payload(9999, "AB12Z", settings.database_id)
    assert lookup_by_payload(session, parse_payload(raw)) is None


def test_lookup_flags_stale_print_code(session: Session) -> None:
    vial = _make_vial(session)
    raw = build_payload(vial.id, "XXXXX", settings.database_id)
    result = lookup_by_payload(session, parse_payload(raw))
    assert result is not None
    assert not result.print_code_matches
    assert result.database_matches
    assert not result.is_fully_consistent


def test_lookup_flags_foreign_database(session: Session) -> None:
    vial = _make_vial(session)
    raw = build_payload(vial.id, vial.print_code, "other-lab")
    result = lookup_by_payload(session, parse_payload(raw))
    assert result is not None
    assert result.print_code_matches
    assert not result.database_matches
