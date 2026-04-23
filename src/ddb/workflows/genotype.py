"""Genotype workflows: update.

Updates are audited so renames and chromosome-notation corrections are
recoverable — both for "what did I call this yesterday?" and for any
downstream Q/A review.
"""

from __future__ import annotations

from typing import Any

from sqlmodel import Session, select

from ddb.models import AuditEvent, Genotype, Vial
from ddb.workflows.vial import GenotypeNotFoundError, WorkflowError


class GenotypeStillHasActiveVialsError(WorkflowError):
    """Raised when dropping a genotype that still has active vials in stock."""

    def __init__(self, genotype_id: int, active_print_codes: list[str]) -> None:
        super().__init__(
            f"genotype_id={genotype_id} still has "
            f"{len(active_print_codes)} active vial(s): "
            f"{', '.join(active_print_codes)}"
        )
        self.genotype_id = genotype_id
        self.active_print_codes = active_print_codes


# Fields a user is allowed to edit via `update_genotype`. Anything not in
# this allow-list is ignored so the GUI can't accidentally scribble onto
# PK / created_at / donor_id.
EDITABLE_FIELDS: frozenset[str] = frozenset(
    {
        "name",
        "chromosome_x",
        "chromosome_2",
        "chromosome_3",
        "chromosome_4",
        "chromosome_y",
        "phenotype",
        "notes",
        "is_wildtype",
        "donor_strain_id",
        "donor_id",
        "is_in_stock",
    }
)


def update_genotype(
    session: Session,
    *,
    genotype_id: int,
    actor_id: int | None = None,
    **fields: Any,
) -> Genotype:
    """Apply the given field updates to an existing genotype.

    Ignores any keys not in EDITABLE_FIELDS so the GUI / API cannot
    silently clobber metadata. A single audit event records `before` /
    `after` snapshots of just the changed fields.
    """
    g = session.get(Genotype, genotype_id)
    if g is None:
        raise GenotypeNotFoundError(f"genotype_id={genotype_id} does not exist")

    updates = {k: v for k, v in fields.items() if k in EDITABLE_FIELDS}
    before: dict[str, Any] = {}
    after: dict[str, Any] = {}
    for k, new in updates.items():
        old = getattr(g, k)
        if old == new:
            continue
        before[k] = old
        after[k] = new
        setattr(g, k, new)

    if not before:
        # Nothing changed — don't spam the audit log.
        return g

    session.add(g)
    session.add(
        AuditEvent(
            actor_id=actor_id,
            entity_type="genotype",
            entity_id=g.id,
            action="update",
            payload={"before": before, "after": after},
        )
    )
    session.commit()
    session.refresh(g)
    return g


def drop_genotype_from_stock(
    session: Session,
    *,
    genotype_id: int,
    actor_id: int | None = None,
) -> Genotype:
    """Mark a genotype as out-of-stock, refusing if any vials are still active.

    The row stays in the DB (so lineage + reports keep working) — it just
    disappears from the New-Vial dropdown and greys in the Genotypes list.
    Pair with an AuditEvent so the transition is recoverable.
    """
    g = session.get(Genotype, genotype_id)
    if g is None:
        raise GenotypeNotFoundError(f"genotype_id={genotype_id} does not exist")
    if not g.is_in_stock:
        return g  # idempotent — already dropped

    # sqlmodel's session.exec(select(single_column)) yields scalars directly.
    active_codes = list(
        session.exec(
            select(Vial.print_code).where(
                Vial.genotype_id == genotype_id,
                Vial.is_active.is_(True),
            )
        ).all()
    )
    if active_codes:
        raise GenotypeStillHasActiveVialsError(genotype_id, active_codes)

    g.is_in_stock = False
    session.add(g)
    session.add(
        AuditEvent(
            actor_id=actor_id,
            entity_type="genotype",
            entity_id=g.id,
            action="drop_from_stock",
            payload={"name": g.name},
        )
    )
    session.commit()
    session.refresh(g)
    return g
