from pathlib import Path

from sqlmodel import Session, select

from ddb.importers.genotype_csv import import_genotypes_csv
from ddb.models import Donor, Genotype

CSV_PATH = Path(__file__).resolve().parent.parent / "docs" / "FlyStockTable.csv"


def test_import_real_csv(session: Session) -> None:
    report = import_genotypes_csv(CSV_PATH, session)

    # 15 rows in source file, all should land first time
    assert report.genotypes_created == 15
    assert report.genotypes_skipped == 0
    assert report.rows_failed == []

    # Donors: AG Goepfert, Carle Japan, Naoyuki Fuse, Bloomington Stock Center, AG Fiala = 5
    donors = session.exec(select(Donor)).all()
    assert {d.name for d in donors} == {
        "AG Goepfert",
        "Carle Japan",
        "Naoyuki Fuse",
        "Bloomington Stock Center",
        "AG Fiala",
    }
    assert report.donors_created == 5


def test_wildtype_flag_round_trips(session: Session) -> None:
    import_genotypes_csv(CSV_PATH, session)
    grazzer = session.exec(select(Genotype).where(Genotype.name == "Grazzer")).one()
    assert grazzer.is_wildtype is True
    assert grazzer.chromosome_x == "+ / +"
    rutabaga = session.exec(select(Genotype).where(Genotype.name == "rutabaga")).one()
    assert rutabaga.is_wildtype is False
    assert rutabaga.donor_strain_id == "9405"


def test_empty_strain_name_falls_back(session: Session) -> None:
    import_genotypes_csv(CSV_PATH, session)
    # row 8 in CSV has empty Strain Name → should become "strain_8"
    placeholder = session.exec(select(Genotype).where(Genotype.name == "strain_8")).one()
    assert placeholder.donor_strain_id == "603"


def test_idempotent_reimport(session: Session) -> None:
    import_genotypes_csv(CSV_PATH, session)
    second = import_genotypes_csv(CSV_PATH, session)
    assert second.genotypes_created == 0
    assert second.genotypes_skipped == 15
    assert second.donors_created == 0
