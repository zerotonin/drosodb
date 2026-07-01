"""Vial workflows: create, flip.

Each workflow is a single transaction that mutates the DB AND writes an
`AuditEvent` in the same commit, so the audit trail cannot diverge from
reality even if the process crashes mid-operation.

The label PNG is written AFTER the commit (a filesystem write can't be
rolled back, but a missing label file is recoverable — just re-render
from DB state).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from ddb.config import settings
from ddb.genotype import format_notation
from ddb.labels import render_label, save_label
from ddb.models import AuditEvent, Genotype, Vial
from ddb.printcode import generate_print_code

MAX_PRINT_CODE_RETRIES = 16


class WorkflowError(Exception):
    pass


class GenotypeNotFoundError(WorkflowError):
    pass


class VialNotFoundError(WorkflowError):
    pass


class CreatedVial(NamedTuple):
    vial: Vial
    label_path: Path


def _unique_print_code(session: Session) -> str:
    """Generate a print code that collides with no ACTIVE vial.

    The partial unique index will ultimately reject duplicates at commit
    time; this loop just pre-checks to avoid retry storms in normal use.
    """
    for _ in range(MAX_PRINT_CODE_RETRIES):
        code = generate_print_code()
        existing = session.exec(
            select(Vial).where(Vial.print_code == code, Vial.is_active.is_(True))
        ).first()
        if existing is None:
            return code
    raise WorkflowError(
        f"Could not find a free print code after {MAX_PRINT_CODE_RETRIES} attempts."
    )


def _render_and_save_label(session: Session, vial: Vial, genotype: Genotype) -> Path:
    owner_username: str | None = None
    if vial.owner_id is not None:
        from ddb.models import User  # local import to avoid a cycle

        owner = session.get(User, vial.owner_id)
        owner_username = owner.username if owner else None

    png = render_label(
        vial_id=vial.id,
        print_code=vial.print_code,
        genotype_name=genotype.name,
        database_id=settings.database_id,
        donor_strain_id=genotype.donor_strain_id,
        owner_username=owner_username,
        generation=vial.generation,
        genotype_notation=format_notation(genotype),
        created_date=vial.created_at.date().isoformat(),
    )
    return save_label(png, settings.data_dir / "labels", vial.print_code)


def create_vial(
    session: Session,
    *,
    genotype_id: int,
    actor_id: int | None = None,
    owner_id: int | None = None,
    org_unit_id: int | None = None,
    notes: str | None = None,
) -> CreatedVial:
    """Insert a new active vial for an existing genotype.

    Commits the DB transaction, THEN writes the label PNG to disk.
    """
    genotype = session.get(Genotype, genotype_id)
    if genotype is None:
        raise GenotypeNotFoundError(f"genotype_id={genotype_id} does not exist")

    vial = Vial(
        print_code=_unique_print_code(session),
        genotype_id=genotype_id,
        owner_id=owner_id,
        org_unit_id=org_unit_id,
        notes=notes,
    )
    session.add(vial)
    try:
        session.flush()  # populate vial.id without committing
    except IntegrityError as e:  # pragma: no cover — racey duplicate
        session.rollback()
        raise WorkflowError("print code collided at commit time") from e

    session.add(
        AuditEvent(
            actor_id=actor_id,
            entity_type="vial",
            entity_id=vial.id,
            action="create",
            payload={
                "print_code": vial.print_code,
                "genotype_id": genotype_id,
                "owner_id": owner_id,
                "org_unit_id": org_unit_id,
            },
        )
    )
    session.commit()
    session.refresh(vial)
    label_path = _render_and_save_label(session, vial, genotype)
    return CreatedVial(vial=vial, label_path=label_path)


def flip_vial(
    session: Session,
    *,
    old_print_code: str,
    actor_id: int | None = None,
    owner_id: int | None = None,
    notes: str | None = None,
) -> CreatedVial:
    """Decommission the active vial with this print code and create its successor.

    Same genotype, new print code, `flipped_from_id` set to the old vial.
    """
    old = session.exec(
        select(Vial).where(Vial.print_code == old_print_code, Vial.is_active.is_(True))
    ).first()
    if old is None:
        raise VialNotFoundError(f"no active vial with print_code={old_print_code!r}")

    genotype = session.get(Genotype, old.genotype_id)
    if genotype is None:  # pragma: no cover — FK would normally prevent this
        raise GenotypeNotFoundError(f"genotype_id={old.genotype_id} missing")

    now = datetime.now(UTC)
    old.is_active = False
    old.decommissioned_at = now
    session.add(old)

    new = Vial(
        print_code=_unique_print_code(session),
        genotype_id=old.genotype_id,
        owner_id=owner_id if owner_id is not None else old.owner_id,
        org_unit_id=old.org_unit_id,
        flipped_from_id=old.id,
        generation=old.generation + 1,
        notes=notes,
    )
    session.add(new)
    session.flush()

    session.add_all(
        [
            AuditEvent(
                actor_id=actor_id,
                entity_type="vial",
                entity_id=old.id,
                action="decommission",
                payload={"reason": "flip", "new_vial_id": new.id},
            ),
            AuditEvent(
                actor_id=actor_id,
                entity_type="vial",
                entity_id=new.id,
                action="flip_from",
                payload={
                    "from_vial_id": old.id,
                    "from_print_code": old_print_code,
                    "print_code": new.print_code,
                    "genotype_id": new.genotype_id,
                },
            ),
        ]
    )
    session.commit()
    session.refresh(new)
    label_path = _render_and_save_label(session, new, genotype)
    return CreatedVial(vial=new, label_path=label_path)


def multiply_vial(
    session: Session,
    *,
    old_print_code: str,
    count: int,
    actor_id: int | None = None,
    owner_id: int | None = None,
    notes: str | None = None,
) -> list[CreatedVial]:
    """Decommission one vial and create `count` successor children.

    A 1-to-N flip: same genotype, owner, and org unit as the parent;
    each child gets a fresh print code, generation+1, and
    `flipped_from_id` pointing at the same parent. Used in the lab when
    one fly stock is split across several fresh food vials.

    All DB mutations happen in a single commit so a crash mid-batch
    can't leave the parent decommissioned without children. Label PNGs
    are rendered after the commit (cheap; recoverable).
    """
    if count < 1:
        raise WorkflowError(f"multiply requires count >= 1 (got {count})")

    old = session.exec(
        select(Vial).where(Vial.print_code == old_print_code, Vial.is_active.is_(True))
    ).first()
    if old is None:
        raise VialNotFoundError(f"no active vial with print_code={old_print_code!r}")

    genotype = session.get(Genotype, old.genotype_id)
    if genotype is None:  # pragma: no cover — FK would normally prevent this
        raise GenotypeNotFoundError(f"genotype_id={old.genotype_id} missing")

    now = datetime.now(UTC)
    old.is_active = False
    old.decommissioned_at = now
    session.add(old)

    new_vials: list[Vial] = []
    for _ in range(count):
        v = Vial(
            print_code=_unique_print_code(session),
            genotype_id=old.genotype_id,
            owner_id=owner_id if owner_id is not None else old.owner_id,
            org_unit_id=old.org_unit_id,
            flipped_from_id=old.id,
            generation=old.generation + 1,
            notes=notes,
        )
        session.add(v)
        new_vials.append(v)
    session.flush()  # populate ids before we reference them in audit payloads

    audit_events: list[AuditEvent] = [
        AuditEvent(
            actor_id=actor_id,
            entity_type="vial",
            entity_id=old.id,
            action="decommission",
            payload={
                "reason": "multiply",
                "new_vial_ids": [v.id for v in new_vials],
                "new_print_codes": [v.print_code for v in new_vials],
            },
        )
    ]
    for v in new_vials:
        audit_events.append(
            AuditEvent(
                actor_id=actor_id,
                entity_type="vial",
                entity_id=v.id,
                action="flip_from",
                payload={
                    "from_vial_id": old.id,
                    "from_print_code": old_print_code,
                    "print_code": v.print_code,
                    "genotype_id": v.genotype_id,
                    "siblings": [sib.print_code for sib in new_vials if sib.id != v.id],
                },
            )
        )
    session.add_all(audit_events)
    session.commit()

    results: list[CreatedVial] = []
    for v in new_vials:
        session.refresh(v)
        label_path = _render_and_save_label(session, v, genotype)
        results.append(CreatedVial(vial=v, label_path=label_path))
    return results


def flip_active_vials_for_genotype(
    session: Session,
    *,
    genotype_id: int,
    actor_id: int | None = None,
) -> list[CreatedVial]:
    """Flip every currently-active vial of `genotype_id` in one transaction.

    Each active parent is decommissioned and a single successor is created
    with the same owner and org unit — equivalent to calling `flip_vial`
    on each print code, except that the whole batch commits together so a
    crash mid-loop cannot leave the DB half-flipped. Audit events for
    every parent/child pair are written in the same commit.

    Returns the created children in the same order the parents were
    found. Empty when the genotype has no active vials (caller decides
    whether to treat that as an error or a no-op).

    Used by the "Flip all active…" button on the Genotypes tab. Bart's
    dark-flies workflow flips 20–40 vials per cycle; running them one at
    a time through the per-vial flip path would burn 20–40 commits with
    no atomicity across them. One transaction, one batch.
    """
    genotype = session.get(Genotype, genotype_id)
    if genotype is None:
        raise GenotypeNotFoundError(f"genotype_id={genotype_id} does not exist")

    parents = list(
        session.exec(
            select(Vial)
            .where(Vial.genotype_id == genotype_id, Vial.is_active.is_(True))
            .order_by(Vial.id)
        ).all()
    )
    if not parents:
        return []

    now = datetime.now(UTC)
    children: list[Vial] = []
    for parent in parents:
        parent.is_active = False
        parent.decommissioned_at = now
        session.add(parent)
        child = Vial(
            print_code=_unique_print_code(session),
            genotype_id=parent.genotype_id,
            owner_id=parent.owner_id,
            org_unit_id=parent.org_unit_id,
            flipped_from_id=parent.id,
            generation=parent.generation + 1,
        )
        session.add(child)
        children.append(child)
    session.flush()  # populate child ids before we reference them in audit payloads

    events: list[AuditEvent] = []
    for parent, child in zip(parents, children, strict=True):
        events.append(
            AuditEvent(
                actor_id=actor_id,
                entity_type="vial",
                entity_id=parent.id,
                action="decommission",
                payload={
                    "reason": "flip_all_for_genotype",
                    "new_vial_id": child.id,
                    "genotype_id": genotype_id,
                },
            )
        )
        events.append(
            AuditEvent(
                actor_id=actor_id,
                entity_type="vial",
                entity_id=child.id,
                action="flip_from",
                payload={
                    "from_vial_id": parent.id,
                    "from_print_code": parent.print_code,
                    "print_code": child.print_code,
                    "genotype_id": child.genotype_id,
                    "batch": "flip_all_for_genotype",
                },
            )
        )
    session.add_all(events)
    session.commit()

    results: list[CreatedVial] = []
    for child in children:
        session.refresh(child)
        label_path = _render_and_save_label(session, child, genotype)
        results.append(CreatedVial(vial=child, label_path=label_path))
    return results


def decommission_vial(
    session: Session,
    *,
    print_code: str,
    actor_id: int | None = None,
    reason: str | None = None,
) -> Vial:
    """Mark an active vial as end-of-life WITHOUT creating a successor.

    Used when a stock is discarded rather than flipped onward.
    """
    vial = session.exec(
        select(Vial).where(Vial.print_code == print_code, Vial.is_active.is_(True))
    ).first()
    if vial is None:
        raise VialNotFoundError(f"no active vial with print_code={print_code!r}")

    vial.is_active = False
    vial.decommissioned_at = datetime.now(UTC)
    session.add(vial)
    session.add(
        AuditEvent(
            actor_id=actor_id,
            entity_type="vial",
            entity_id=vial.id,
            action="decommission",
            payload={"reason": reason or "end-of-life", "new_vial_id": None},
        )
    )
    session.commit()
    session.refresh(vial)
    return vial


def active_flip_descendant_codes(session: Session, vial_id: int) -> list[str]:
    """Return print codes of currently-active vials downstream of `vial_id`.

    Walks the flip-chain (flipped_from_id) forward. Used to warn the user
    before reactivating a decommissioned vial: if an active descendant
    exists, reactivating creates a fork in the lineage.
    """
    out: list[str] = []
    frontier = [vial_id]
    seen: set[int] = {vial_id}
    while frontier:
        next_frontier: list[int] = []
        for parent_id in frontier:
            children = session.exec(select(Vial).where(Vial.flipped_from_id == parent_id)).all()
            for child in children:
                if child.id in seen:
                    continue
                seen.add(child.id)
                if child.is_active:
                    out.append(child.print_code)
                next_frontier.append(child.id)
        frontier = next_frontier
    return sorted(out)


def reactivate_vial(
    session: Session,
    *,
    print_code: str,
    actor_id: int | None = None,
) -> Vial:
    """Undo a decommission — mark a vial as active again.

    Idempotent if already active. Audited so the round-trip stays
    visible. Does NOT auto-decommission active descendants; if the
    caller wants to avoid a forked lineage, they should check
    `active_flip_descendant_codes` first and handle it explicitly.
    """
    vial = session.exec(select(Vial).where(Vial.print_code == print_code)).first()
    if vial is None:
        raise VialNotFoundError(f"no vial with print_code={print_code!r}")
    if vial.is_active:
        return vial  # idempotent

    descendants = active_flip_descendant_codes(session, vial.id)
    vial.is_active = True
    vial.decommissioned_at = None
    session.add(vial)
    session.add(
        AuditEvent(
            actor_id=actor_id,
            entity_type="vial",
            entity_id=vial.id,
            action="reactivate",
            payload={
                "print_code": vial.print_code,
                "active_descendants": descendants,
            },
        )
    )
    session.commit()
    session.refresh(vial)
    return vial
