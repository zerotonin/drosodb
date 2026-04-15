"""Flexible vial search + full vial detail for the GUI and CLI.

One filterable `search_vials` function replaces the eight report variants
listed in the spec (all active, by user, by genotype, by gene text, by
donor, by status, by org unit, vial detail) — any combination of filters
composes into the same join-and-where query. Rows come back denormalised
so UIs never do N+1 lookups.

`vial_detail` returns everything the scan panel needs in one shot.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from sqlalchemy import or_
from sqlmodel import Session, select

from ddb.models import (
    AuditEvent,
    Donor,
    Genotype,
    OrgUnit,
    User,
    Vial,
    VialParentage,
)

DEFAULT_SEARCH_LIMIT = 500


@dataclass
class VialRow:
    """One row in a vial search result. Flat and JSON-serialisable."""

    vial_id: int
    print_code: str
    generation: int
    is_active: bool
    genotype_id: int
    genotype_name: str
    phenotype: str | None
    is_wildtype: bool
    owner_username: str | None
    owner_full_name: str | None
    org_unit_name: str | None
    donor_name: str | None
    donor_strain_id: str | None
    created_at: datetime
    decommissioned_at: datetime | None
    notes: str | None


def search_vials(
    session: Session,
    *,
    active: bool | None = None,
    genotype_name_contains: str | None = None,
    gene_contains: str | None = None,
    owner_username: str | None = None,
    org_unit_name: str | None = None,
    donor_name: str | None = None,
    donor_strain_id: str | None = None,
    generation: int | None = None,
    print_code: str | None = None,
    created_after: date | None = None,
    created_before: date | None = None,
    limit: int = DEFAULT_SEARCH_LIMIT,
) -> list[VialRow]:
    """All filters are optional and AND-combined. Returns newest vials first."""
    stmt = (
        select(Vial, Genotype, User, OrgUnit, Donor)
        .join(Genotype, Vial.genotype_id == Genotype.id)
        .join(User, Vial.owner_id == User.id, isouter=True)
        .join(OrgUnit, Vial.org_unit_id == OrgUnit.id, isouter=True)
        .join(Donor, Genotype.donor_id == Donor.id, isouter=True)
    )

    if active is not None:
        stmt = stmt.where(Vial.is_active.is_(active))
    if generation is not None:
        stmt = stmt.where(Vial.generation == generation)
    if print_code is not None:
        stmt = stmt.where(Vial.print_code == print_code.upper())
    if owner_username is not None:
        stmt = stmt.where(User.username == owner_username)
    if org_unit_name is not None:
        stmt = stmt.where(OrgUnit.name == org_unit_name)
    if donor_name is not None:
        stmt = stmt.where(Donor.name == donor_name)
    if donor_strain_id is not None:
        stmt = stmt.where(Genotype.donor_strain_id == donor_strain_id)
    if genotype_name_contains is not None:
        stmt = stmt.where(Genotype.name.ilike(f"%{genotype_name_contains}%"))
    if gene_contains is not None:
        pat = f"%{gene_contains}%"
        stmt = stmt.where(
            or_(
                Genotype.chromosome_x.ilike(pat),
                Genotype.chromosome_2.ilike(pat),
                Genotype.chromosome_3.ilike(pat),
                Genotype.chromosome_4.ilike(pat),
                Genotype.chromosome_y.ilike(pat),
                Genotype.phenotype.ilike(pat),
                Genotype.name.ilike(pat),
            )
        )
    if created_after is not None:
        stmt = stmt.where(Vial.created_at >= datetime.combine(created_after, datetime.min.time()))
    if created_before is not None:
        stmt = stmt.where(Vial.created_at < datetime.combine(created_before, datetime.min.time()))

    stmt = stmt.order_by(Vial.id.desc()).limit(limit)

    rows: list[VialRow] = []
    for vial, geno, user, org, donor in session.exec(stmt).all():
        rows.append(
            VialRow(
                vial_id=vial.id,
                print_code=vial.print_code,
                generation=vial.generation,
                is_active=vial.is_active,
                genotype_id=geno.id,
                genotype_name=geno.name,
                phenotype=geno.phenotype,
                is_wildtype=geno.is_wildtype,
                owner_username=user.username if user else None,
                owner_full_name=user.full_name if user else None,
                org_unit_name=org.name if org else None,
                donor_name=donor.name if donor else None,
                donor_strain_id=geno.donor_strain_id,
                created_at=vial.created_at,
                decommissioned_at=vial.decommissioned_at,
                notes=vial.notes,
            )
        )
    return rows


@dataclass
class AuditRow:
    action: str
    created_at: datetime
    actor_username: str | None
    payload: dict | None


@dataclass
class VialDetail:
    """Everything the scan panel needs about a scanned vial."""

    row: VialRow
    parent_flip_print_code: str | None  # flipped_from
    parent_cross_print_codes: list[str] = field(default_factory=list)
    child_flip_print_codes: list[str] = field(default_factory=list)
    child_cross_print_codes: list[str] = field(default_factory=list)
    audit: list[AuditRow] = field(default_factory=list)


def vial_detail(session: Session, vial_id: int) -> VialDetail | None:
    vial = session.get(Vial, vial_id)
    if vial is None:
        return None

    base = search_vials(session, print_code=vial.print_code, limit=1)
    if not base:
        return None
    row = base[0]

    parent_flip: str | None = None
    if vial.flipped_from_id is not None:
        p = session.get(Vial, vial.flipped_from_id)
        parent_flip = p.print_code if p else None

    cross_parents = []
    for pid in session.exec(
        select(VialParentage.parent_id).where(VialParentage.child_id == vial.id)
    ).all():
        p = session.get(Vial, pid)
        if p:
            cross_parents.append(p.print_code)

    flip_children = []
    for cid in session.exec(select(Vial.id).where(Vial.flipped_from_id == vial.id)).all():
        c = session.get(Vial, cid)
        if c:
            flip_children.append(c.print_code)

    cross_children = []
    for cid in session.exec(
        select(VialParentage.child_id).where(VialParentage.parent_id == vial.id)
    ).all():
        c = session.get(Vial, cid)
        if c:
            cross_children.append(c.print_code)

    audit_rows: list[AuditRow] = []
    events = session.exec(
        select(AuditEvent, User)
        .join(User, AuditEvent.actor_id == User.id, isouter=True)
        .where(AuditEvent.entity_type == "vial", AuditEvent.entity_id == vial.id)
        .order_by(AuditEvent.created_at.desc())
    ).all()
    for ev, actor in events:
        audit_rows.append(
            AuditRow(
                action=ev.action,
                created_at=ev.created_at,
                actor_username=actor.username if actor else None,
                payload=ev.payload,
            )
        )

    return VialDetail(
        row=row,
        parent_flip_print_code=parent_flip,
        parent_cross_print_codes=sorted(cross_parents),
        child_flip_print_codes=sorted(flip_children),
        child_cross_print_codes=sorted(cross_children),
        audit=audit_rows,
    )
