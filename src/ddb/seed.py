"""Populate the DB with a realistic demo dataset so new users can see how
the system behaves.

Users are the medical officers from the various ships named Enterprise
across the Star Trek franchise. If you want to reset and re-seed:

    rm ddb.sqlite3 && alembic upgrade head && ddb seed-demo
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path

from sqlmodel import Session, select

from ddb.importers.genotype_csv import import_genotypes_csv
from ddb.models import Genotype, OrgUnit, User
from ddb.workflows import create_vial, decommission_vial, flip_vial


@dataclass(frozen=True)
class Officer:
    username: str
    full_name: str
    email: str
    note: str


OFFICERS: tuple[Officer, ...] = (
    Officer("phlox", "Phlox", "phlox@enterprise.starfleet", "NX-01 (Denobulan)"),
    Officer("lmccoy", "Leonard H. McCoy", "lmccoy@enterprise.starfleet", "NCC-1701"),
    Officer("cchapel", "Christine Chapel", "cchapel@enterprise.starfleet", "NCC-1701"),
    Officer("mbenga", "Joseph M'Benga", "mbenga@enterprise.starfleet", "NCC-1701"),
    Officer("bcrusher", "Beverly Crusher", "bcrusher@enterprise.starfleet", "NCC-1701-D"),
    Officer("kpulaski", "Katherine Pulaski", "kpulaski@enterprise.starfleet", "NCC-1701-D"),
)

UNIT_NAME = "Sickbay"


@dataclass
class SeedReport:
    users_created: int = 0
    units_created: int = 0
    genotypes_present: int = 0
    vials_created: int = 0
    vials_flipped: int = 0
    vials_decommissioned: int = 0


def _ensure_users(session: Session) -> list[User]:
    existing = {u.username: u for u in session.exec(select(User)).all()}
    created = 0
    for o in OFFICERS:
        if o.username in existing:
            continue
        u = User(username=o.username, full_name=o.full_name, email=o.email)
        session.add(u)
        existing[o.username] = u
        created += 1
    session.commit()
    for o in OFFICERS:
        session.refresh(existing[o.username])
    return [existing[o.username] for o in OFFICERS], created  # type: ignore[return-value]


def _ensure_unit(session: Session) -> tuple[OrgUnit, int]:
    unit = session.exec(select(OrgUnit).where(OrgUnit.name == UNIT_NAME)).first()
    if unit is not None:
        return unit, 0
    unit = OrgUnit(name=UNIT_NAME, description="Demo organisational unit.")
    session.add(unit)
    session.commit()
    session.refresh(unit)
    return unit, 1


def seed_demo(
    session: Session,
    *,
    csv_path: Path | None = None,
    seed: int = 1701,
) -> SeedReport:
    """Idempotent-ish: will not duplicate users, but WILL create more vials on
    every run. Intended to be run once on a fresh DB."""
    rng = random.Random(seed)
    report = SeedReport()

    # Genotypes — only import if the DB is empty.
    existing_genos = session.exec(select(Genotype)).all()
    if not existing_genos:
        path = csv_path or Path("docs") / "FlyStockTable.csv"
        import_report = import_genotypes_csv(path, session)
        report.genotypes_present = import_report.genotypes_created
    else:
        report.genotypes_present = len(existing_genos)

    genotypes = session.exec(select(Genotype).order_by(Genotype.id)).all()
    users, report.users_created = _ensure_users(session)
    unit, report.units_created = _ensure_unit(session)

    # 1-2 initial vials per genotype, round-robin assigned to officers.
    initials = []
    for i, g in enumerate(genotypes):
        count = 1 if rng.random() < 0.5 else 2
        for _ in range(count):
            owner = users[i % len(users)]
            res = create_vial(
                session,
                genotype_id=g.id,
                actor_id=owner.id,
                owner_id=owner.id,
                org_unit_id=unit.id,
            )
            initials.append(res.vial.print_code)
            report.vials_created += 1

    # Flip a chunk of them once, then flip a smaller chunk again — this
    # gives us generation 2 and 3 vials in the demo DB.
    gen2_pool = rng.sample(initials, k=max(1, len(initials) // 2))
    gen2_codes: list[str] = []
    for code in gen2_pool:
        owner = users[rng.randrange(len(users))]
        res = flip_vial(session, old_print_code=code, actor_id=owner.id, owner_id=owner.id)
        gen2_codes.append(res.vial.print_code)
        report.vials_flipped += 1

    gen3_pool = rng.sample(gen2_codes, k=max(1, len(gen2_codes) // 3))
    for code in gen3_pool:
        owner = users[rng.randrange(len(users))]
        flip_vial(session, old_print_code=code, actor_id=owner.id, owner_id=owner.id)
        report.vials_flipped += 1

    # Decommission a couple of originals outright (stock discarded, no successor).
    leftover = [c for c in initials if c not in gen2_pool]
    for code in leftover[:2]:
        decommission_vial(session, print_code=code, reason="demo — discarded")
        report.vials_decommissioned += 1

    return report
