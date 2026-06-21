"""Tests for the biosafety report.

Cover:
  - Snapshot counts (vials active/decommissioned, genotypes in-stock/dropped).
  - Period counts (new vials, flips, decommissions split by reason,
    reactivations, genotype add/drop).
  - Org-unit and owner scope filters limit vial counts but not genotypes.
  - Helper period functions (last-N-months, annual).
  - PDF renders to disk (smoke test — file exists, non-zero size, is PDF).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlmodel import Session

from ddb.biosec import (
    gather_biosec_report,
    period_from_annual,
    period_last_n_months,
)
from ddb.biosec.pdf import render_biosec_pdf
from ddb.config import settings
from ddb.models import Genotype, OrgUnit, User
from ddb.workflows import (
    create_vial,
    decommission_vial,
    drop_genotype_from_stock,
    flip_vial,
    multiply_vial,
    reactivate_vial,
)


@pytest.fixture(autouse=True)
def redirect_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    return tmp_path


def _seed_world(session: Session) -> dict:
    """Seed: 2 org units, 3 users, 4 genotypes, a handful of vials."""
    lab_a = OrgUnit(name="Lab A")
    lab_b = OrgUnit(name="Lab B")
    bart = User(username="bart", full_name="Bart")
    alex = User(username="alex", full_name="Alex")
    irene = User(username="irene", full_name="Irene")
    canton = Genotype(name="Canton-S", is_wildtype=True)
    rutabaga = Genotype(name="rutabaga", donor_strain_id="9405")
    dunce = Genotype(name="dunce", donor_strain_id="9407")
    obsolete = Genotype(name="obsolete-strain")
    session.add_all([lab_a, lab_b, bart, alex, irene, canton, rutabaga, dunce, obsolete])
    session.commit()
    return {
        "lab_a": lab_a,
        "lab_b": lab_b,
        "bart": bart,
        "alex": alex,
        "irene": irene,
        "canton": canton,
        "rutabaga": rutabaga,
        "dunce": dunce,
        "obsolete": obsolete,
    }


def test_snapshot_counts_basic(session: Session) -> None:
    w = _seed_world(session)
    # 3 active vials, 1 will be decommissioned.
    v1 = create_vial(session, genotype_id=w["canton"].id, owner_id=w["bart"].id).vial
    create_vial(session, genotype_id=w["rutabaga"].id, owner_id=w["alex"].id)
    create_vial(session, genotype_id=w["dunce"].id, owner_id=w["irene"].id)
    decommission_vial(session, print_code=v1.print_code, reason="contaminated")
    drop_genotype_from_stock(session, genotype_id=w["obsolete"].id)

    now = datetime.now(UTC)
    report = gather_biosec_report(
        session,
        from_=now.replace(year=now.year - 1),
        to=now,
        now=now,
    )

    assert report.snapshot.active_vials == 2  # v1 decommissioned
    assert report.snapshot.decommissioned_vials == 1
    assert report.snapshot.in_stock_genotypes == 3  # canton + rutabaga + dunce
    assert report.snapshot.dropped_genotypes == 1


def test_period_counts_split_flip_vs_standalone_decommissions(session: Session) -> None:
    w = _seed_world(session)
    v1 = create_vial(session, genotype_id=w["canton"].id).vial
    v2 = create_vial(session, genotype_id=w["rutabaga"].id).vial
    v3 = create_vial(session, genotype_id=w["dunce"].id).vial

    # v1 flipped (1-to-1): 1 flip_from event + 1 decom event (reason='flip')
    flip_vial(session, old_print_code=v1.print_code)
    # v2 multiplied: 1 decom (reason='multiply') + N flip_from events
    multiply_vial(session, old_print_code=v2.print_code, count=3)
    # v3 decommissioned standalone (reason='trashed')
    decommission_vial(session, print_code=v3.print_code, reason="trashed")

    now = datetime.now(UTC)
    report = gather_biosec_report(session, from_=now.replace(year=now.year - 1), to=now, now=now)

    # flip + multiply both write flip_from on every successor.
    assert report.period.vials_flipped == 1 + 3
    assert report.period.vials_decommissioned_standalone == 1  # the trashed one
    assert report.period.vials_decommissioned_via_flip == 2  # 1 flip + 1 multiply
    # 3 originals + 1 successor (from flip) + 3 successors (from multiply)
    assert report.period.new_vials == 3 + 1 + 3


def test_period_counts_reactivations(session: Session) -> None:
    w = _seed_world(session)
    v = create_vial(session, genotype_id=w["canton"].id).vial
    decommission_vial(session, print_code=v.print_code, reason="oops")
    reactivate_vial(session, print_code=v.print_code)

    now = datetime.now(UTC)
    report = gather_biosec_report(session, from_=now.replace(year=now.year - 1), to=now, now=now)
    assert report.period.vials_reactivated == 1


def test_org_unit_scope_filters_vial_counts(session: Session) -> None:
    w = _seed_world(session)
    create_vial(
        session, genotype_id=w["canton"].id, owner_id=w["bart"].id, org_unit_id=w["lab_a"].id
    )
    create_vial(
        session, genotype_id=w["rutabaga"].id, owner_id=w["alex"].id, org_unit_id=w["lab_a"].id
    )
    create_vial(
        session, genotype_id=w["dunce"].id, owner_id=w["irene"].id, org_unit_id=w["lab_b"].id
    )

    now = datetime.now(UTC)
    full = gather_biosec_report(session, from_=now.replace(year=now.year - 1), to=now, now=now)
    scoped = gather_biosec_report(
        session,
        from_=now.replace(year=now.year - 1),
        to=now,
        org_unit_id=w["lab_a"].id,
        now=now,
    )

    assert full.snapshot.active_vials == 3
    assert scoped.snapshot.active_vials == 2  # Lab A only
    # Genotype counts are global — they don't shrink under a scope filter.
    assert scoped.snapshot.in_stock_genotypes == full.snapshot.in_stock_genotypes


def test_genotype_listing_includes_zero_vial_strains(session: Session) -> None:
    """A genotype with no live vials still appears in the annex listing —
    biosafety wants to see we have the strain on the books, even if the
    freezer's empty right now."""
    w = _seed_world(session)
    create_vial(session, genotype_id=w["canton"].id)
    # rutabaga and dunce have no vials at all.

    now = datetime.now(UTC)
    report = gather_biosec_report(session, from_=now.replace(year=now.year - 1), to=now, now=now)
    names_to_counts = {g.name: g.active_vial_count for g in report.genotype_listing}
    assert names_to_counts["Canton-S"] == 1
    assert names_to_counts["rutabaga"] == 0
    assert names_to_counts["dunce"] == 0
    # The dropped one is excluded (it's already in snapshot.dropped_genotypes).
    assert "obsolete-strain" in names_to_counts  # still in stock here


def test_owner_breakdown_ranks_by_active_vial_count(session: Session) -> None:
    w = _seed_world(session)
    for _ in range(3):
        create_vial(session, genotype_id=w["canton"].id, owner_id=w["bart"].id)
    create_vial(session, genotype_id=w["rutabaga"].id, owner_id=w["alex"].id)

    now = datetime.now(UTC)
    report = gather_biosec_report(session, from_=now.replace(year=now.year - 1), to=now, now=now)
    usernames = [o.owner_username for o in report.owner_breakdown]
    # Bart is the heaviest user → first row.
    assert usernames[0] == "bart"
    assert usernames[1] == "alex"


def test_period_last_n_months_returns_window_ending_now() -> None:
    now = datetime(2025, 6, 15, tzinfo=UTC)
    start, end = period_last_n_months(now, months=3)
    assert end == now
    # Approximate (30 days × 3); just check the window is positive.
    delta_days = (end - start).days
    assert 85 < delta_days < 95


def test_period_from_annual_covers_full_year() -> None:
    start, end = period_from_annual(2024)
    assert start == datetime(2024, 1, 1, tzinfo=UTC)
    assert end == datetime(2024, 12, 31, 23, 59, 59, tzinfo=UTC)


def test_pdf_renders_to_disk(session: Session, tmp_path: Path) -> None:
    """Smoke test: PDF file exists, has non-trivial size, starts with %PDF."""
    w = _seed_world(session)
    create_vial(session, genotype_id=w["canton"].id, owner_id=w["bart"].id)
    create_vial(session, genotype_id=w["rutabaga"].id, owner_id=w["alex"].id)

    now = datetime.now(UTC)
    report = gather_biosec_report(session, from_=now.replace(year=now.year - 1), to=now, now=now)
    out_path = render_biosec_pdf(report, tmp_path / "biosec.pdf")
    assert out_path.exists()
    head = out_path.read_bytes()[:4]
    assert head == b"%PDF", f"expected a PDF header, got {head!r}"
    assert out_path.stat().st_size > 1500  # any real PDF is way bigger
