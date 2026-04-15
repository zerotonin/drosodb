from pathlib import Path

import typer
from sqlmodel import Session

from ddb.db import engine, init_db
from ddb.importers.genotype_csv import import_genotypes_csv

app = typer.Typer(help="DDB — Drosophila Vial Tracking System")


@app.command("init-db")
def init_db_cmd() -> None:
    """Create all tables in the configured database (dev/test only)."""
    init_db()
    typer.echo("Database initialised.")


@app.command("import-genotypes")
def import_genotypes_cmd(csv_path: Path) -> None:
    """Import genotypes from a FlyStockTable-format CSV."""
    with Session(engine) as session:
        report = import_genotypes_csv(csv_path, session)
    typer.echo(
        f"Created {report.genotypes_created} genotype(s), "
        f"{report.donors_created} donor(s); "
        f"skipped {report.genotypes_skipped} existing."
    )
    if report.rows_failed:
        for row_no, reason in report.rows_failed:
            typer.echo(f"  row {row_no}: {reason}", err=True)


if __name__ == "__main__":
    app()
