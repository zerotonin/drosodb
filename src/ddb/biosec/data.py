"""Pure SQL gatherer for the biosafety report.

`gather_biosec_report` returns a `BiosecReport` dataclass with:

  * **Snapshot counts** at the report's end date — active vs
    decommissioned vials, in-stock vs dropped genotypes.
  * **Period counts** between the report's start and end dates — new
    vials, flips, decommissions (standalone and flip-driven, counted
    separately), reactivations, new genotypes, dropped genotypes.
  * **Per-owner active-vial breakdown** so a Head of Lab can see who
    holds which slice of the stock at the report date.
  * **Full active-genotype listing** so the document is self-contained
    for institutional biosafety filings — the user asked specifically
    for a full listing (not a top-N summary) even if it spills onto
    multiple pages.

Scope filters (`org_unit_id`, `owner_id`) are applied to vial counts.
Genotype counts are global because a Drosophila stock entry isn't
"owned" by an org unit the way a vial is.

No reportlab imports here — the PDF renderer takes a `BiosecReport`
instance.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlmodel import Session, func, select

from ddb.models import AuditEvent, Genotype, OrgUnit, User, Vial


@dataclass(frozen=True)
class SnapshotCounts:
    """State of the world at the report's end date."""

    active_vials: int
    decommissioned_vials: int
    in_stock_genotypes: int
    dropped_genotypes: int

    @property
    def total_vials(self) -> int:
        return self.active_vials + self.decommissioned_vials

    @property
    def total_genotypes(self) -> int:
        return self.in_stock_genotypes + self.dropped_genotypes


@dataclass(frozen=True)
class PeriodCounts:
    """Activity between the report's start and end dates."""

    new_vials: int
    vials_flipped: int  # flip_from audit events
    vials_decommissioned_standalone: int  # decommission with reason not in {flip, multiply}
    vials_decommissioned_via_flip: int  # decommission with reason in {flip, multiply}
    vials_reactivated: int
    new_genotypes: int
    genotypes_dropped: int
    genotypes_reactivated: int


@dataclass(frozen=True)
class OwnerCount:
    """One row in the per-owner active-vial table."""

    owner_username: str | None
    owner_full_name: str | None
    active_vials: int


@dataclass(frozen=True)
class GenotypeRow:
    """One row in the full active-genotype listing.

    `notation` is the canonical `X ; II ; III [; IV]` chromosome
    string built by `ddb.genotype.format_notation` — the same string
    that appears on every label. Biosafety/regulatory readers care
    about what the strain actually IS, not just its short name.
    `phenotype` and `notes` are forwarded verbatim from the Genotype
    row so the annex is self-contained for filings.
    """

    name: str
    notation: str
    donor_strain_id: str | None
    is_wildtype: bool
    phenotype: str | None
    notes: str | None
    active_vial_count: int


@dataclass
class BiosecReport:
    """Top-level dataclass passed to the PDF renderer."""

    period_from: datetime
    period_to: datetime
    generated_at: datetime
    org_unit_name: str | None  # None = whole database
    owner_username: str | None  # None = all owners
    snapshot: SnapshotCounts
    period: PeriodCounts
    owner_breakdown: list[OwnerCount] = field(default_factory=list)
    genotype_listing: list[GenotypeRow] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Convenience period helpers — used by the CLI and GUI
# ---------------------------------------------------------------------------


def period_last_n_months(now: datetime, *, months: int = 3) -> tuple[datetime, datetime]:
    """Return (from, to) covering the last `months` calendar months ending now.

    Uses a 30-day-per-month approximation. The biosec report is for
    institutional bookkeeping, not actuarial precision, so a fixed
    rolling window is fine.
    """
    end = now
    start = end - timedelta(days=30 * months)
    return start, end


def period_from_annual(year: int) -> tuple[datetime, datetime]:
    """Return (Jan 1 00:00 UTC, Dec 31 23:59:59 UTC) for `year`."""
    start = datetime(year, 1, 1, tzinfo=UTC)
    end = datetime(year, 12, 31, 23, 59, 59, tzinfo=UTC)
    return start, end


# ---------------------------------------------------------------------------
# Main gatherer
# ---------------------------------------------------------------------------


def gather_biosec_report(
    session: Session,
    *,
    from_: datetime,
    to: datetime,
    org_unit_id: int | None = None,
    owner_id: int | None = None,
    now: datetime | None = None,
) -> BiosecReport:
    """Build a `BiosecReport` for the given window and optional scope.

    `from_` and `to` are inclusive on both sides for the period activity
    counts. The snapshot uses `to` as its as-of timestamp (i.e. counts
    things active at that moment).
    """
    if now is None:
        now = datetime.now(UTC)

    org_unit_name = _resolve_org_unit_name(session, org_unit_id)
    owner_username = _resolve_username(session, owner_id)

    snapshot = _snapshot(session, as_of=to, org_unit_id=org_unit_id, owner_id=owner_id)
    period = _period(session, from_=from_, to=to, org_unit_id=org_unit_id, owner_id=owner_id)
    owners = _owner_breakdown(session, org_unit_id=org_unit_id)
    genos = _genotype_listing(session)

    return BiosecReport(
        period_from=from_,
        period_to=to,
        generated_at=now,
        org_unit_name=org_unit_name,
        owner_username=owner_username,
        snapshot=snapshot,
        period=period,
        owner_breakdown=owners,
        genotype_listing=genos,
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _resolve_org_unit_name(session: Session, org_unit_id: int | None) -> str | None:
    if org_unit_id is None:
        return None
    row = session.get(OrgUnit, org_unit_id)
    return row.name if row else None


def _resolve_username(session: Session, owner_id: int | None) -> str | None:
    if owner_id is None:
        return None
    row = session.get(User, owner_id)
    return row.username if row else None


def _scope_vials(stmt, org_unit_id: int | None, owner_id: int | None):
    """Apply the org-unit / owner filters to a Vial SELECT statement."""
    if org_unit_id is not None:
        stmt = stmt.where(Vial.org_unit_id == org_unit_id)
    if owner_id is not None:
        stmt = stmt.where(Vial.owner_id == owner_id)
    return stmt


def _count(session: Session, stmt) -> int:
    """Run a count(*) variant of `stmt` and return the integer."""
    return int(session.exec(stmt).one() or 0)


def _snapshot(
    session: Session,
    *,
    as_of: datetime,
    org_unit_id: int | None,
    owner_id: int | None,
) -> SnapshotCounts:
    """Counts that describe the state of the database at `as_of`.

    A vial counts as ACTIVE at `as_of` iff it was created on or before
    `as_of` AND (it is still active OR it was decommissioned after
    `as_of`). This means the snapshot at an old date reflects the
    state at that date, not "active right now".
    """
    active_stmt = select(func.count(Vial.id)).where(
        Vial.created_at <= as_of,
        (Vial.is_active == True) | (Vial.decommissioned_at > as_of),  # noqa: E712
    )
    active_stmt = _scope_vials(active_stmt, org_unit_id, owner_id)

    decom_stmt = select(func.count(Vial.id)).where(
        Vial.created_at <= as_of,
        Vial.is_active == False,  # noqa: E712
        Vial.decommissioned_at != None,  # noqa: E711
        Vial.decommissioned_at <= as_of,
    )
    decom_stmt = _scope_vials(decom_stmt, org_unit_id, owner_id)

    in_stock_stmt = select(func.count(Genotype.id)).where(
        Genotype.created_at <= as_of,
        Genotype.is_in_stock == True,  # noqa: E712
    )
    dropped_stmt = select(func.count(Genotype.id)).where(
        Genotype.created_at <= as_of,
        Genotype.is_in_stock == False,  # noqa: E712
    )

    return SnapshotCounts(
        active_vials=_count(session, active_stmt),
        decommissioned_vials=_count(session, decom_stmt),
        in_stock_genotypes=_count(session, in_stock_stmt),
        dropped_genotypes=_count(session, dropped_stmt),
    )


def _period(
    session: Session,
    *,
    from_: datetime,
    to: datetime,
    org_unit_id: int | None,
    owner_id: int | None,
) -> PeriodCounts:
    """Counts that describe activity between `from_` and `to`."""

    new_vials_stmt = select(func.count(Vial.id)).where(
        Vial.created_at >= from_, Vial.created_at <= to
    )
    new_vials_stmt = _scope_vials(new_vials_stmt, org_unit_id, owner_id)

    # For audit-driven counts we cross-reference the AuditEvent.entity_id
    # with the scoped Vial.id set so org_unit/owner filters carry through.
    scoped_vial_ids = _scope_vials(select(Vial.id), org_unit_id, owner_id)

    flipped = _count(
        session,
        select(func.count(AuditEvent.id)).where(
            AuditEvent.entity_type == "vial",
            AuditEvent.action == "flip_from",
            AuditEvent.created_at >= from_,
            AuditEvent.created_at <= to,
            AuditEvent.entity_id.in_(scoped_vial_ids),
        ),
    )
    reactivated = _count(
        session,
        select(func.count(AuditEvent.id)).where(
            AuditEvent.entity_type == "vial",
            AuditEvent.action == "reactivate",
            AuditEvent.created_at >= from_,
            AuditEvent.created_at <= to,
            AuditEvent.entity_id.in_(scoped_vial_ids),
        ),
    )

    # Decommission events split by reason — flip/multiply decommissions
    # are "lineage-driven", standalone decommissions are "end-of-life".
    decom_events = session.exec(
        select(AuditEvent).where(
            AuditEvent.entity_type == "vial",
            AuditEvent.action == "decommission",
            AuditEvent.created_at >= from_,
            AuditEvent.created_at <= to,
            AuditEvent.entity_id.in_(scoped_vial_ids),
        )
    ).all()
    flip_driven = 0
    standalone = 0
    for ev in decom_events:
        reason = (ev.payload or {}).get("reason", "")
        if reason in ("flip", "multiply"):
            flip_driven += 1
        else:
            standalone += 1

    new_genos = _count(
        session,
        select(func.count(Genotype.id)).where(
            Genotype.created_at >= from_, Genotype.created_at <= to
        ),
    )
    dropped = _count(
        session,
        select(func.count(AuditEvent.id)).where(
            AuditEvent.entity_type == "genotype",
            AuditEvent.action == "drop_from_stock",
            AuditEvent.created_at >= from_,
            AuditEvent.created_at <= to,
        ),
    )
    geno_reactivated = _count(
        session,
        select(func.count(AuditEvent.id)).where(
            AuditEvent.entity_type == "genotype",
            AuditEvent.action == "reactivate_in_stock",
            AuditEvent.created_at >= from_,
            AuditEvent.created_at <= to,
        ),
    )

    return PeriodCounts(
        new_vials=_count(session, new_vials_stmt),
        vials_flipped=flipped,
        vials_decommissioned_standalone=standalone,
        vials_decommissioned_via_flip=flip_driven,
        vials_reactivated=reactivated,
        new_genotypes=new_genos,
        genotypes_dropped=dropped,
        genotypes_reactivated=geno_reactivated,
    )


def _owner_breakdown(session: Session, *, org_unit_id: int | None) -> list[OwnerCount]:
    """One row per User who currently owns at least one active vial."""
    stmt = (
        select(
            User.username,
            User.full_name,
            func.count(Vial.id).label("n"),
        )
        .join(User, Vial.owner_id == User.id, isouter=True)
        .where(Vial.is_active == True)  # noqa: E712
        .group_by(User.username, User.full_name)
        .order_by(func.count(Vial.id).desc())
    )
    if org_unit_id is not None:
        stmt = stmt.where(Vial.org_unit_id == org_unit_id)
    rows = session.exec(stmt).all()
    return [
        OwnerCount(owner_username=u, owner_full_name=f, active_vials=int(n)) for (u, f, n) in rows
    ]


def _genotype_listing(session: Session) -> list[GenotypeRow]:
    """Every in-stock Genotype with its current active-vial count,
    full chromosome notation, donor strain id, phenotype, and notes.

    Sorted by name (case-insensitive). Dropped genotypes are omitted —
    they're already counted in `snapshot.dropped_genotypes`. A genotype
    with zero current vials still appears with `active_vial_count=0`,
    so biosafety can see "strain is on the books but no live stock
    right now".
    """
    from ddb.genotype import format_notation

    genotypes = session.exec(
        select(Genotype)
        .where(Genotype.is_in_stock == True)  # noqa: E712
        .order_by(func.lower(Genotype.name))
    ).all()

    out: list[GenotypeRow] = []
    for g in genotypes:
        n = _count(
            session,
            select(func.count(Vial.id)).where(
                Vial.genotype_id == g.id,
                Vial.is_active == True,  # noqa: E712
            ),
        )
        out.append(
            GenotypeRow(
                name=g.name,
                notation=format_notation(g),
                donor_strain_id=g.donor_strain_id,
                is_wildtype=bool(g.is_wildtype),
                phenotype=g.phenotype,
                notes=g.notes,
                active_vial_count=n,
            )
        )
    return out
