"""Walk a vial's lineage across both flip and cross relationships.

Two independent relationships can link vials:
  - `Vial.flipped_from_id` (direct parent via flip / copy)
  - `VialParentage` (cross — one child, N parents)

Both are followed, so the result is a DAG that's ready for the future
cross workflow even though we're only flipping today.

Returns sorted rows suitable for CSV export or a UI table.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from sqlmodel import Session, select

from ddb.models import Donor, Genotype, User, Vial, VialParentage


@dataclass
class LineageRow:
    vial_id: int
    print_code: str
    generation: int
    is_active: bool
    genotype_name: str
    genotype_id: int
    owner_username: str | None
    donor_name: str | None
    donor_strain_id: str | None
    flipped_from_id: int | None
    flipped_from_print_code: str | None
    parent_print_codes: str  # comma-separated (cross parents, if any)
    created_at: str
    decommissioned_at: str | None
    notes: str | None

    @classmethod
    def headers(cls) -> list[str]:
        return [f.name for f in cls.__dataclass_fields__.values()]  # type: ignore[attr-defined]

    def as_row(self) -> list[object]:
        return [getattr(self, h) for h in self.headers()]


def _parents_of(session: Session, vial_id: int) -> set[int]:
    parents: set[int] = set()
    v = session.get(Vial, vial_id)
    if v and v.flipped_from_id:
        parents.add(v.flipped_from_id)
    cross = session.exec(
        select(VialParentage.parent_id).where(VialParentage.child_id == vial_id)
    ).all()
    parents.update(cross)
    return parents


def _children_of(session: Session, vial_id: int) -> set[int]:
    children: set[int] = set()
    flips = session.exec(select(Vial.id).where(Vial.flipped_from_id == vial_id)).all()
    children.update(flips)
    cross = session.exec(
        select(VialParentage.child_id).where(VialParentage.parent_id == vial_id)
    ).all()
    children.update(cross)
    return children


def lineage_ids(session: Session, vial_id: int) -> set[int]:
    """All vial ids reachable upward or downward from the given vial (inclusive)."""
    seen: set[int] = {vial_id}

    # Up.
    stack = [vial_id]
    while stack:
        v = stack.pop()
        for p in _parents_of(session, v):
            if p not in seen:
                seen.add(p)
                stack.append(p)

    # Down.
    stack = [vial_id]
    while stack:
        v = stack.pop()
        for c in _children_of(session, v):
            if c not in seen:
                seen.add(c)
                stack.append(c)
    return seen


def _row(session: Session, vial: Vial) -> LineageRow:
    geno = session.get(Genotype, vial.genotype_id)
    owner = session.get(User, vial.owner_id) if vial.owner_id else None
    donor = session.get(Donor, geno.donor_id) if geno and geno.donor_id else None

    flipped_from_code = None
    if vial.flipped_from_id is not None:
        parent = session.get(Vial, vial.flipped_from_id)
        flipped_from_code = parent.print_code if parent else None

    parent_codes: list[str] = []
    for pp in session.exec(
        select(VialParentage.parent_id).where(VialParentage.child_id == vial.id)
    ).all():
        p = session.get(Vial, pp)
        if p:
            parent_codes.append(p.print_code)

    return LineageRow(
        vial_id=vial.id,
        print_code=vial.print_code,
        generation=vial.generation,
        is_active=vial.is_active,
        genotype_name=geno.name if geno else "",
        genotype_id=vial.genotype_id,
        owner_username=owner.username if owner else None,
        donor_name=donor.name if donor else None,
        donor_strain_id=geno.donor_strain_id if geno else None,
        flipped_from_id=vial.flipped_from_id,
        flipped_from_print_code=flipped_from_code,
        parent_print_codes=",".join(sorted(parent_codes)),
        created_at=vial.created_at.isoformat(timespec="seconds"),
        decommissioned_at=(
            vial.decommissioned_at.isoformat(timespec="seconds") if vial.decommissioned_at else None
        ),
        notes=vial.notes,
    )


def lineage_for(session: Session, vial_id: int) -> list[LineageRow]:
    """Return the full lineage for a vial, sorted by (generation, vial_id)."""
    ids = lineage_ids(session, vial_id)
    rows = []
    for vid in ids:
        v = session.get(Vial, vid)
        if v is not None:
            rows.append(_row(session, v))
    rows.sort(key=lambda r: (r.generation, r.vial_id))
    return rows


def export_lineage_csv(session: Session, vial_id: int, out_path: Path) -> Path:
    rows = lineage_for(session, vial_id)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(LineageRow.headers())
        for r in rows:
            writer.writerow(r.as_row())
    return out_path
