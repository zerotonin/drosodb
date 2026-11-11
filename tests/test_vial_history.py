"""Audit trail (ddb.vial_history) and genealogy graph (ddb.lineage.lineage_graph)."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlmodel import Session

from ddb.config import settings
from ddb.lineage import lineage_graph
from ddb.models import AuditEvent, Genotype, OrgUnit, User, VialParentage
from ddb.vial_history import export_trail_csv, vial_audit_trail
from ddb.workflows import (
    create_vial,
    decommission_vial,
    flip_vial,
    multiply_vial,
    reactivate_vial,
)


@pytest.fixture(autouse=True)
def redirect_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Keep label PNGs out of the repo."""
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    return tmp_path


@pytest.fixture()
def family(session: Session) -> dict[str, object]:
    """grandparent → parent → (3 multiplied children); one child retired + revived."""
    user = User(username="ella")
    unit = OrgUnit(name="Fly room")
    geno = Genotype(name="nlg4 KO70")
    session.add_all([user, unit, geno])
    session.commit()

    root = create_vial(
        session, genotype_id=geno.id, actor_id=user.id, owner_id=user.id, org_unit_id=unit.id
    ).vial
    parent = flip_vial(session, old_print_code=root.print_code, actor_id=user.id).vial
    children = [
        c.vial
        for c in multiply_vial(session, old_print_code=parent.print_code, count=3, actor_id=user.id)
    ]
    decommission_vial(session, print_code=children[0].print_code, actor_id=user.id, reason="mites")
    reactivate_vial(session, print_code=children[0].print_code, actor_id=user.id)
    return {
        "user": user,
        "unit": unit,
        "geno": geno,
        "root": root,
        "parent": parent,
        "children": children,
    }


# ─────────────────────────────────────────────────────────────────
#  Audit trail
# ─────────────────────────────────────────────────────────────────


def test_trail_names_vials_people_and_genotype(session: Session, family: dict) -> None:
    root = family["root"]
    trail = vial_audit_trail(session, root.id)
    assert [e.label for e in trail] == ["Created", "Flipped into"]
    assert trail[0].summary == "Genotype nlg4 KO70 · owner ella · unit Fly room"
    assert trail[0].actor == "ella"
    assert trail[1].summary == family["parent"].print_code


def test_trail_describes_multiply_both_ways(session: Session, family: dict) -> None:
    parent, children = family["parent"], family["children"]
    parent_trail = vial_audit_trail(session, parent.id)
    assert parent_trail[-1].label == "Multiplied into"
    assert parent_trail[-1].summary == ", ".join(c.print_code for c in children)

    child_trail = vial_audit_trail(session, children[1].id)
    assert child_trail[0].label == "Multiplied from"
    assert parent.print_code in child_trail[0].summary
    assert children[0].print_code in child_trail[0].summary  # sibling listed


def test_trail_keeps_manual_reason_and_reactivation_in_order(
    session: Session, family: dict
) -> None:
    trail = vial_audit_trail(session, family["children"][0].id)
    assert [e.label for e in trail] == ["Multiplied from", "Decommissioned", "Reactivated"]
    assert trail[1].summary == "Reason: mites"
    assert trail == sorted(trail, key=lambda e: e.when)


def test_unknown_action_falls_back_to_readable_text(session: Session, family: dict) -> None:
    vial = family["root"]
    session.add(
        AuditEvent(
            entity_type="vial", entity_id=vial.id, action="label_reprint", payload={"copies": 2}
        )
    )
    session.commit()
    last = vial_audit_trail(session, vial.id)[-1]
    assert (last.label, last.summary) == ("Label reprint", "copies: 2")


def test_missing_ids_render_as_hash_ids(session: Session, family: dict) -> None:
    vial = family["root"]
    session.add(
        AuditEvent(
            entity_type="vial",
            entity_id=vial.id,
            action="decommission",
            payload={"reason": "flip", "new_vial_id": 99999},
        )
    )
    session.commit()
    assert vial_audit_trail(session, vial.id)[-1].summary == "#99999"


def test_export_trail_csv_round_trips_unicode(
    session: Session, family: dict, tmp_path: Path
) -> None:
    trail = vial_audit_trail(session, family["root"].id)
    out = export_trail_csv(trail, tmp_path / "out" / "trail.csv")
    lines = out.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "when_utc,actor,action,label,summary"
    assert len(lines) == 1 + len(trail)
    assert "·" in lines[1]


# ─────────────────────────────────────────────────────────────────
#  Genealogy graph
# ─────────────────────────────────────────────────────────────────


def test_graph_from_middle_reaches_both_directions(session: Session, family: dict) -> None:
    parent, children = family["parent"], family["children"]
    graph = lineage_graph(session, parent.id)
    assert graph.focus_id == parent.id
    assert graph.ancestor_ids == {family["root"].id}
    assert graph.descendant_ids == {c.id for c in children}
    assert len(graph.edges) == 4
    assert all(e.kind == "flip" for e in graph.edges)
    assert graph.nodes[parent.id].genotype_name == "nlg4 KO70"


def test_graph_from_leaf_excludes_siblings(session: Session, family: dict) -> None:
    leaf = family["children"][2]
    graph = lineage_graph(session, leaf.id)
    assert set(graph.nodes) == {family["root"].id, family["parent"].id, leaf.id}
    assert graph.descendant_ids == set()


def test_graph_includes_cross_parents_with_role(session: Session, family: dict) -> None:
    mother, father = family["children"][1], family["children"][2]
    offspring = create_vial(session, genotype_id=family["geno"].id).vial
    session.add_all(
        [
            VialParentage(child_id=offspring.id, parent_id=mother.id, role="mother"),
            VialParentage(child_id=offspring.id, parent_id=father.id, role="father"),
        ]
    )
    session.commit()
    graph = lineage_graph(session, offspring.id)
    roles = {(e.parent_id, e.kind) for e in graph.edges if e.child_id == offspring.id}
    assert roles == {(mother.id, "mother"), (father.id, "father")}


def test_graph_unknown_vial_raises(session: Session) -> None:
    with pytest.raises(LookupError):
        lineage_graph(session, 424242)
