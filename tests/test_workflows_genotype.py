"""update_genotype workflow tests."""

from __future__ import annotations

import pytest
from sqlmodel import Session, select

from ddb.models import AuditEvent, Genotype
from ddb.workflows import update_genotype
from ddb.workflows.vial import GenotypeNotFoundError


def _seed(session: Session) -> int:
    g = Genotype(name="strain_10", chromosome_2="nompC / Cyo", is_wildtype=False)
    session.add(g)
    session.commit()
    return g.id


def test_update_renames_and_audits(session: Session) -> None:
    gid = _seed(session)
    update_genotype(session, genotype_id=gid, name="nompC rescue")

    g = session.get(Genotype, gid)
    assert g.name == "nompC rescue"

    events = session.exec(
        select(AuditEvent).where(AuditEvent.entity_type == "genotype", AuditEvent.entity_id == gid)
    ).all()
    assert len(events) == 1
    ev = events[0]
    assert ev.action == "update"
    assert ev.payload["before"] == {"name": "strain_10"}
    assert ev.payload["after"] == {"name": "nompC rescue"}


def test_update_ignores_unknown_fields(session: Session) -> None:
    """Random kwargs mustn't leak onto the row (defence against a typo'd GUI)."""
    gid = _seed(session)
    update_genotype(session, genotype_id=gid, name="x", totally_made_up_field="boom")
    g = session.get(Genotype, gid)
    assert g.name == "x"  # allowed change applied
    assert not hasattr(g, "totally_made_up_field")


def test_update_is_noop_when_nothing_changes(session: Session) -> None:
    gid = _seed(session)
    update_genotype(session, genotype_id=gid, name="strain_10")  # same value

    events = session.exec(select(AuditEvent).where(AuditEvent.entity_type == "genotype")).all()
    assert events == [], "no-op update should not write an audit event"


def test_update_missing_genotype_raises(session: Session) -> None:
    with pytest.raises(GenotypeNotFoundError):
        update_genotype(session, genotype_id=9999, name="ghost")


def test_update_chromosomes_changes_notation(session: Session) -> None:
    """Renaming a chromosome slot should propagate into format_notation()."""
    from ddb.genotype import format_notation

    gid = _seed(session)
    before = format_notation(session.get(Genotype, gid))
    assert "nompC / Cyo" in before

    update_genotype(session, genotype_id=gid, chromosome_2="GAL4-nompC / CyO")
    after = format_notation(session.get(Genotype, gid))
    assert "GAL4-nompC / CyO" in after
    assert "nompC / Cyo" not in after
