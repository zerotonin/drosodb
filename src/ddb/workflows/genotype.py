"""Genotype workflows: update.

Updates are audited so renames and chromosome-notation corrections are
recoverable — both for "what did I call this yesterday?" and for any
downstream Q/A review.
"""

from __future__ import annotations

from typing import Any

from sqlmodel import Session

from ddb.models import AuditEvent, Genotype
from ddb.workflows.vial import GenotypeNotFoundError

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
