"""Verify the active-print-code partial unique index.

Tests use a real on-disk migrated DB so the index from migration
c270ff9405be is exercised, not just `create_all`.
"""

from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, create_engine

from alembic import command
from ddb.models import Genotype, Vial

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def migrated_session(tmp_path: Path) -> Session:
    db_path = tmp_path / "ddb.sqlite3"
    url = f"sqlite:///{db_path}"

    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")

    engine = create_engine(url)
    with Session(engine) as s:
        yield s


def test_active_print_code_must_be_unique(migrated_session: Session) -> None:
    s = migrated_session
    g = Genotype(name="g1")
    s.add(g)
    s.commit()

    s.add(Vial(print_code="DUP01", genotype_id=g.id, is_active=True))
    s.commit()

    s.add(Vial(print_code="DUP01", genotype_id=g.id, is_active=True))
    with pytest.raises(IntegrityError):
        s.commit()
    s.rollback()


def test_decommissioned_print_code_can_be_reused(migrated_session: Session) -> None:
    s = migrated_session
    g = Genotype(name="g2")
    s.add(g)
    s.commit()

    old = Vial(print_code="REUSE", genotype_id=g.id, is_active=False)
    s.add(old)
    s.commit()

    new = Vial(print_code="REUSE", genotype_id=g.id, is_active=True)
    s.add(new)
    s.commit()  # must succeed
    assert new.id != old.id
