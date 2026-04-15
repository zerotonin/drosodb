from pathlib import Path

import pytest
from sqlmodel import Session, select

from ddb.config import settings
from ddb.models import AuditEvent, Genotype, User
from ddb.workflows import (
    GenotypeNotFoundError,
    VialNotFoundError,
    create_vial,
    flip_vial,
)


@pytest.fixture(autouse=True)
def redirect_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Keep label PNGs from leaking into the repo during tests."""
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    return tmp_path


def _seed(session: Session) -> tuple[User, Genotype]:
    user = User(username="bart")
    geno = Genotype(name="Canton-S", is_wildtype=True)
    session.add_all([user, geno])
    session.commit()
    return user, geno


def test_create_vial_writes_audit_and_label(session: Session, tmp_path: Path) -> None:
    user, geno = _seed(session)

    result = create_vial(session, genotype_id=geno.id, actor_id=user.id, owner_id=user.id)

    assert result.vial.id is not None
    assert result.vial.is_active is True
    assert result.vial.genotype_id == geno.id
    assert result.label_path.exists()
    assert result.label_path.parent == tmp_path / "labels"

    events = session.exec(select(AuditEvent).where(AuditEvent.entity_id == result.vial.id)).all()
    assert len(events) == 1
    assert events[0].action == "create"
    assert events[0].payload["print_code"] == result.vial.print_code


def test_create_vial_with_unknown_genotype_raises(session: Session) -> None:
    with pytest.raises(GenotypeNotFoundError):
        create_vial(session, genotype_id=9999)


def test_flip_decommissions_old_and_creates_new(session: Session) -> None:
    user, geno = _seed(session)
    first = create_vial(session, genotype_id=geno.id, actor_id=user.id).vial
    original_code = first.print_code

    flipped = flip_vial(session, old_print_code=original_code, actor_id=user.id)

    assert flipped.vial.print_code != original_code
    assert flipped.vial.flipped_from_id == first.id
    assert flipped.vial.genotype_id == geno.id
    assert flipped.vial.is_active is True

    session.refresh(first)
    assert first.is_active is False
    assert first.decommissioned_at is not None

    events_old = session.exec(select(AuditEvent).where(AuditEvent.entity_id == first.id)).all()
    events_new = session.exec(
        select(AuditEvent).where(AuditEvent.entity_id == flipped.vial.id)
    ).all()
    actions_old = [e.action for e in events_old]
    actions_new = [e.action for e in events_new]
    assert "create" in actions_old and "decommission" in actions_old
    assert actions_new == ["flip_from"]
    assert events_new[0].payload["from_print_code"] == original_code


def test_flip_unknown_print_code_raises(session: Session) -> None:
    with pytest.raises(VialNotFoundError):
        flip_vial(session, old_print_code="NOPE0")


def test_flip_refuses_to_flip_already_decommissioned(session: Session) -> None:
    _, geno = _seed(session)
    v = create_vial(session, genotype_id=geno.id).vial
    flip_vial(session, old_print_code=v.print_code)
    # Original code is now inactive; trying to flip it again must fail.
    with pytest.raises(VialNotFoundError):
        flip_vial(session, old_print_code=v.print_code)
