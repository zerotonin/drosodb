from pathlib import Path

import pytest
from sqlmodel import Session

from ddb.config import settings
from ddb.lineage import export_lineage_csv, lineage_for
from ddb.models import Genotype
from ddb.seed import OFFICERS, seed_demo
from ddb.workflows import create_vial, decommission_vial, flip_vial


@pytest.fixture(autouse=True)
def _labels_in_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "data_dir", tmp_path)


def _seed_tiny(session: Session):
    session.add(Genotype(name="Canton-S"))
    session.commit()


def test_generation_starts_at_one_and_increments(session: Session) -> None:
    _seed_tiny(session)
    geno_id = session.exec(__import__("sqlmodel").select(Genotype)).one().id

    v1 = create_vial(session, genotype_id=geno_id).vial
    assert v1.generation == 1

    v2 = flip_vial(session, old_print_code=v1.print_code).vial
    v3 = flip_vial(session, old_print_code=v2.print_code).vial
    assert v2.generation == 2
    assert v3.generation == 3


def test_lineage_follows_flip_chain_both_directions(session: Session) -> None:
    _seed_tiny(session)
    geno_id = session.exec(__import__("sqlmodel").select(Genotype)).one().id
    v1 = create_vial(session, genotype_id=geno_id).vial
    v2 = flip_vial(session, old_print_code=v1.print_code).vial
    v3 = flip_vial(session, old_print_code=v2.print_code).vial

    # Starting from the middle vial, we still get the whole chain.
    rows = lineage_for(session, v2.id)
    codes = [r.print_code for r in rows]
    assert set(codes) == {v1.print_code, v2.print_code, v3.print_code}
    # Sorted by generation:
    assert [r.generation for r in rows] == [1, 2, 3]


def test_decommission_marks_inactive_and_keeps_it_in_lineage(session: Session) -> None:
    _seed_tiny(session)
    geno_id = session.exec(__import__("sqlmodel").select(Genotype)).one().id
    v = create_vial(session, genotype_id=geno_id).vial

    decommissioned = decommission_vial(session, print_code=v.print_code, reason="unit test")
    assert decommissioned.is_active is False
    assert decommissioned.decommissioned_at is not None

    # Still appears in its own lineage, just as inactive.
    rows = lineage_for(session, v.id)
    assert len(rows) == 1
    assert rows[0].is_active is False


def test_lineage_csv_export(tmp_path: Path, session: Session) -> None:
    _seed_tiny(session)
    geno_id = session.exec(__import__("sqlmodel").select(Genotype)).one().id
    v1 = create_vial(session, genotype_id=geno_id).vial
    flip_vial(session, old_print_code=v1.print_code)

    out = tmp_path / "lineage.csv"
    export_lineage_csv(session, v1.id, out)
    content = out.read_text()
    lines = content.strip().splitlines()
    assert lines[0].startswith("vial_id,print_code,generation")
    assert len(lines) == 3  # header + 2 rows


def test_seed_demo_creates_officers_and_lineage(session: Session) -> None:
    report = seed_demo(session, csv_path=Path("docs") / "FlyStockTable.csv", seed=1701)
    assert report.users_created == len(OFFICERS)
    assert report.vials_created > 0
    assert report.vials_flipped > 0

    from sqlmodel import select

    from ddb.models import User, Vial

    usernames = {u.username for u in session.exec(select(User)).all()}
    for o in OFFICERS:
        assert o.username in usernames

    gen2_or_more = session.exec(select(Vial).where(Vial.generation >= 2)).all()
    assert len(gen2_or_more) > 0
