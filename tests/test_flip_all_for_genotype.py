"""Tests for `flip_active_vials_for_genotype` — the batch-flip workflow
behind the "Flip all active…" button on the Genotypes tab.

Covers:
  - N active vials → N children, N old vials decommissioned in one commit
  - Inactive / decommissioned parents are ignored
  - Only vials of the requested genotype are touched
  - Empty batch (no active vials) returns []
  - Unknown genotype id raises GenotypeNotFoundError
  - Audit trail: `flip_all_for_genotype` batch marker, per-parent
    decommission, per-child flip_from with parent linkage
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlmodel import Session, select

from ddb.config import settings
from ddb.models import AuditEvent, Genotype, User
from ddb.workflows import (
    GenotypeNotFoundError,
    create_vial,
    decommission_vial,
    flip_active_vials_for_genotype,
)


@pytest.fixture(autouse=True)
def redirect_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    return tmp_path


def _seed(session: Session) -> tuple[User, Genotype, Genotype]:
    user = User(username="bart")
    dark = Genotype(name="darkfly")
    other = Genotype(name="Canton-S", is_wildtype=True)
    session.add_all([user, dark, other])
    session.commit()
    return user, dark, other


def test_flips_every_active_vial_and_produces_labels(session: Session) -> None:
    user, dark, _ = _seed(session)
    parents = [create_vial(session, genotype_id=dark.id, actor_id=user.id).vial for _ in range(3)]
    parent_codes = [p.print_code for p in parents]

    results = flip_active_vials_for_genotype(session, genotype_id=dark.id, actor_id=user.id)

    assert len(results) == 3
    assert all(r.label_path.exists() for r in results)

    for parent in parents:
        session.refresh(parent)
        assert parent.is_active is False
        assert parent.decommissioned_at is not None

    # Children point back at their own parents (not a many-to-one mixup).
    parent_by_id = {p.id: p for p in parents}
    for r in results:
        assert r.vial.is_active is True
        assert r.vial.genotype_id == dark.id
        assert r.vial.flipped_from_id in parent_by_id
        parent = parent_by_id[r.vial.flipped_from_id]
        assert r.vial.generation == parent.generation + 1
        assert r.vial.print_code not in parent_codes


def test_ignores_already_decommissioned_vials(session: Session) -> None:
    user, dark, _ = _seed(session)
    keep = create_vial(session, genotype_id=dark.id, actor_id=user.id).vial
    dead = create_vial(session, genotype_id=dark.id, actor_id=user.id).vial
    decommission_vial(session, print_code=dead.print_code, actor_id=user.id, reason="test")

    results = flip_active_vials_for_genotype(session, genotype_id=dark.id, actor_id=user.id)

    assert len(results) == 1
    assert results[0].vial.flipped_from_id == keep.id


def test_only_touches_the_requested_genotype(session: Session) -> None:
    user, dark, other = _seed(session)
    dark_vials = [create_vial(session, genotype_id=dark.id).vial for _ in range(2)]
    other_vials = [create_vial(session, genotype_id=other.id).vial for _ in range(2)]

    results = flip_active_vials_for_genotype(session, genotype_id=dark.id, actor_id=user.id)

    assert len(results) == 2
    for v in other_vials:
        session.refresh(v)
        assert v.is_active is True
        assert v.decommissioned_at is None
    for v in dark_vials:
        session.refresh(v)
        assert v.is_active is False


def test_empty_batch_returns_empty_list(session: Session) -> None:
    _, dark, _ = _seed(session)
    results = flip_active_vials_for_genotype(session, genotype_id=dark.id)
    assert results == []


def test_unknown_genotype_raises(session: Session) -> None:
    with pytest.raises(GenotypeNotFoundError):
        flip_active_vials_for_genotype(session, genotype_id=9999)


def test_audit_trail_has_batch_marker_and_pairs(session: Session) -> None:
    user, dark, _ = _seed(session)
    parents = [create_vial(session, genotype_id=dark.id, actor_id=user.id).vial for _ in range(2)]

    results = flip_active_vials_for_genotype(session, genotype_id=dark.id, actor_id=user.id)

    parent_by_id = {p.id: p for p in parents}
    for r in results:
        parent = parent_by_id[r.vial.flipped_from_id]
        dec_events = session.exec(
            select(AuditEvent).where(
                AuditEvent.entity_id == parent.id, AuditEvent.action == "decommission"
            )
        ).all()
        assert any(
            e.payload.get("reason") == "flip_all_for_genotype"
            and e.payload.get("new_vial_id") == r.vial.id
            for e in dec_events
        )

        flip_events = session.exec(
            select(AuditEvent).where(
                AuditEvent.entity_id == r.vial.id, AuditEvent.action == "flip_from"
            )
        ).all()
        assert len(flip_events) == 1
        payload = flip_events[0].payload
        assert payload["batch"] == "flip_all_for_genotype"
        assert payload["from_vial_id"] == parent.id
        assert payload["from_print_code"] == parent.print_code
        assert payload["print_code"] == r.vial.print_code


def test_children_of_active_only_get_created(session: Session) -> None:
    """A vial's flip descendant is a different vial. Confirm the batch
    doesn't accidentally re-flip its own newborn children in the same
    call (the SELECT snapshot is taken before we start mutating)."""
    user, dark, _ = _seed(session)
    create_vial(session, genotype_id=dark.id, actor_id=user.id)

    first = flip_active_vials_for_genotype(session, genotype_id=dark.id, actor_id=user.id)
    assert len(first) == 1

    # Now the previously-flipped child is the only active vial. Second
    # call must flip exactly that one, not two.
    second = flip_active_vials_for_genotype(session, genotype_id=dark.id, actor_id=user.id)
    assert len(second) == 1
    session.refresh(first[0].vial)
    assert first[0].vial.is_active is False


def test_child_owner_and_org_unit_carry_over(session: Session) -> None:
    from ddb.models import OrgUnit

    user, dark, _ = _seed(session)
    owner = User(username="alice")
    unit = OrgUnit(name="Lab X")
    session.add_all([owner, unit])
    session.commit()

    parent = create_vial(
        session,
        genotype_id=dark.id,
        owner_id=owner.id,
        org_unit_id=unit.id,
    ).vial

    results = flip_active_vials_for_genotype(session, genotype_id=dark.id)
    assert len(results) == 1
    child = results[0].vial
    assert child.owner_id == owner.id
    assert child.org_unit_id == unit.id
    assert child.flipped_from_id == parent.id
