from pathlib import Path

import pytest
from sqlmodel import Session, select

from ddb.config import settings
from ddb.models import AuditEvent, Genotype, User
from ddb.workflows import (
    GenotypeNotFoundError,
    VialNotFoundError,
    WorkflowError,
    active_flip_descendant_codes,
    create_vial,
    decommission_vial,
    flip_vial,
    multiply_vial,
    reactivate_vial,
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


def test_reactivate_vial_restores_active_state_and_audits(session: Session) -> None:
    user, geno = _seed(session)
    v = create_vial(session, genotype_id=geno.id, actor_id=user.id).vial
    decommission_vial(session, print_code=v.print_code, actor_id=user.id, reason="oops")
    session.refresh(v)
    assert v.is_active is False

    reactivate_vial(session, print_code=v.print_code, actor_id=user.id)

    session.refresh(v)
    assert v.is_active is True
    assert v.decommissioned_at is None

    events = session.exec(select(AuditEvent).where(AuditEvent.entity_id == v.id)).all()
    actions = [e.action for e in events]
    assert actions.count("reactivate") == 1
    react_event = next(e for e in events if e.action == "reactivate")
    assert react_event.payload["active_descendants"] == []


def test_reactivate_is_idempotent_on_active_vial(session: Session) -> None:
    _, geno = _seed(session)
    v = create_vial(session, genotype_id=geno.id).vial
    # Vial is already active — a reactivate should be a no-op (no audit).
    reactivate_vial(session, print_code=v.print_code)

    events = session.exec(select(AuditEvent).where(AuditEvent.entity_id == v.id)).all()
    actions = [e.action for e in events]
    assert "reactivate" not in actions


def test_reactivate_unknown_print_code_raises(session: Session) -> None:
    with pytest.raises(VialNotFoundError):
        reactivate_vial(session, print_code="NOPE0")


def test_reactivate_after_flip_reports_active_descendants(session: Session) -> None:
    """Reactivating a vial that was decommissioned by a flip must record
    the active successor in the audit payload, so the lineage fork is
    visible in the event log."""
    user, geno = _seed(session)
    original = create_vial(session, genotype_id=geno.id, actor_id=user.id).vial
    successor = flip_vial(
        session, old_print_code=original.print_code, actor_id=user.id
    ).vial
    session.refresh(original)
    assert original.is_active is False
    assert successor.is_active is True

    descendants = active_flip_descendant_codes(session, original.id)
    assert descendants == [successor.print_code]

    reactivate_vial(session, print_code=original.print_code, actor_id=user.id)

    session.refresh(original)
    session.refresh(successor)
    assert original.is_active is True
    assert successor.is_active is True  # NOT auto-decommissioned

    react_event = session.exec(
        select(AuditEvent).where(
            AuditEvent.entity_id == original.id,
            AuditEvent.action == "reactivate",
        )
    ).first()
    assert react_event is not None
    assert react_event.payload["active_descendants"] == [successor.print_code]


def test_multiply_creates_n_children_from_one_parent(session: Session) -> None:
    user, geno = _seed(session)
    parent = create_vial(session, genotype_id=geno.id, actor_id=user.id).vial
    parent_code = parent.print_code

    results = multiply_vial(
        session, old_print_code=parent_code, count=4, actor_id=user.id
    )

    assert len(results) == 4
    session.refresh(parent)
    assert parent.is_active is False
    assert parent.decommissioned_at is not None

    codes = {r.vial.print_code for r in results}
    assert len(codes) == 4  # each child has a unique print code
    assert parent_code not in codes

    for r in results:
        assert r.vial.flipped_from_id == parent.id
        assert r.vial.genotype_id == geno.id
        assert r.vial.is_active is True
        assert r.vial.generation == parent.generation + 1
        assert r.label_path.exists()

    # Audit: 1 decommission on parent + 4 flip_from events on children.
    parent_events = session.exec(
        select(AuditEvent).where(AuditEvent.entity_id == parent.id)
    ).all()
    actions = [e.action for e in parent_events]
    assert actions.count("decommission") == 1
    decom = next(e for e in parent_events if e.action == "decommission")
    assert decom.payload["reason"] == "multiply"
    assert len(decom.payload["new_vial_ids"]) == 4
    assert set(decom.payload["new_print_codes"]) == codes

    for r in results:
        child_events = session.exec(
            select(AuditEvent).where(AuditEvent.entity_id == r.vial.id)
        ).all()
        assert [e.action for e in child_events] == ["flip_from"]
        ff = child_events[0]
        assert ff.payload["from_print_code"] == parent_code
        # Siblings list excludes self.
        assert r.vial.print_code not in ff.payload["siblings"]
        assert len(ff.payload["siblings"]) == 3


def test_multiply_unknown_print_code_raises(session: Session) -> None:
    with pytest.raises(VialNotFoundError):
        multiply_vial(session, old_print_code="NOPE0", count=3)


def test_multiply_refuses_zero_or_negative_count(session: Session) -> None:
    _, geno = _seed(session)
    v = create_vial(session, genotype_id=geno.id).vial
    with pytest.raises(WorkflowError):
        multiply_vial(session, old_print_code=v.print_code, count=0)


def test_multiply_of_already_decommissioned_vial_raises(session: Session) -> None:
    _, geno = _seed(session)
    v = create_vial(session, genotype_id=geno.id).vial
    decommission_vial(session, print_code=v.print_code)
    with pytest.raises(VialNotFoundError):
        multiply_vial(session, old_print_code=v.print_code, count=3)


def test_active_flip_descendant_codes_walks_multi_generation_chain(
    session: Session,
) -> None:
    _, geno = _seed(session)
    g0 = create_vial(session, genotype_id=geno.id).vial
    g1 = flip_vial(session, old_print_code=g0.print_code).vial
    g2 = flip_vial(session, old_print_code=g1.print_code).vial

    # g0 is two generations back — its active descendant is g2.
    assert active_flip_descendant_codes(session, g0.id) == [g2.print_code]
    # g2 itself has no descendants.
    assert active_flip_descendant_codes(session, g2.id) == []
