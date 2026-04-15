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


def _render_and_save_label(vial: Vial, genotype: Genotype) -> Path:
    png = render_label(
        vial_id=vial.id,
        print_code=vial.print_code,
        genotype_name=genotype.name,
        database_id=settings.database_id,
        donor_strain_id=genotype.donor_strain_id,
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
    label_path = _render_and_save_label(vial, genotype)
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
    label_path = _render_and_save_label(new, genotype)
    return CreatedVial(vial=new, label_path=label_path)
