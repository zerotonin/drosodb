from pathlib import Path

import typer
from sqlmodel import Session, select

from ddb.camera.capture import preview, resolve_role
from ddb.camera.config import load_assignments, save_assignments
from ddb.camera.enumeration import discover_cameras
from ddb.db import engine, init_db
from ddb.importers.genotype_csv import import_genotypes_csv
from ddb.models import Genotype
from ddb.workflows import VialNotFoundError, WorkflowError, create_vial, flip_vial

app = typer.Typer(help="DDB — Drosophila Vial Tracking System")
camera_app = typer.Typer(help="Detect, assign, and preview cameras.")
vial_app = typer.Typer(help="Vial workflows: create, flip.")
app.add_typer(camera_app, name="camera")
app.add_typer(vial_app, name="vial")


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


@camera_app.command("list")
def camera_list() -> None:
    """Show detected cameras and their currently assigned roles."""
    cams = discover_cameras()
    assignments = load_assignments()
    reverse = {bp: role for role, bp in assignments.roles.items()}
    if not cams:
        typer.echo("No cameras detected.")
        return
    typer.echo(f"{'ROLE':<8} {'BUS':<10} {'DEVICE':<14} NAME")
    for c in cams:
        role = reverse.get(c.bus_path, "-")
        typer.echo(f"{role:<8} {c.bus_path:<10} {c.device_path:<14} {c.name}")


@camera_app.command("assign")
def camera_assign(
    duration: float = typer.Option(4.0, help="Seconds to preview each camera."),
) -> None:
    """Interactively label each camera as 'front', 'back', or skip."""
    cams = discover_cameras()
    if not cams:
        typer.echo("No cameras detected.", err=True)
        raise typer.Exit(1)

    assignments = load_assignments()
    valid = {"front", "back", "skip"}
    for cam in cams:
        typer.echo(f"\nShowing {cam.device_path} (bus {cam.bus_path}) — {cam.name}")
        typer.echo(f"  (preview {duration:.1f}s; press 'q' to close early)")
        preview(cam.device_path, window_title=f"assign: {cam.bus_path}", duration_s=duration)
        while True:
            answer = typer.prompt("  role? [front/back/skip]", default="skip").strip().lower()
            if answer in valid:
                break
            typer.echo("    please type front, back, or skip")
        if answer != "skip":
            assignments.set(answer, cam.bus_path)

    save_assignments(assignments)
    typer.echo("\nSaved assignments:")
    for role, bp in sorted(assignments.roles.items()):
        typer.echo(f"  {role}: {bp}")


@camera_app.command("preview")
def camera_preview(role: str) -> None:
    """Open a live preview window for the camera assigned to this role."""
    cam = resolve_role(role)
    typer.echo(f"Previewing {role} — {cam.device_path} (bus {cam.bus_path}). Press 'q' to close.")
    preview(cam.device_path, window_title=f"{role} camera")


def _resolve_genotype(session: Session, genotype: str) -> Genotype | None:
    """Accept a numeric id or an exact name."""
    if genotype.isdigit():
        return session.get(Genotype, int(genotype))
    return session.exec(select(Genotype).where(Genotype.name == genotype)).first()


@vial_app.command("create")
def vial_create_cmd(
    genotype: str = typer.Argument(..., help="Genotype id or exact name."),
    owner_id: int | None = typer.Option(None, "--owner-id"),
    notes: str | None = typer.Option(None, "--notes"),
) -> None:
    """Create a new active vial and render its label PNG."""
    with Session(engine) as session:
        geno = _resolve_genotype(session, genotype)
        if geno is None:
            typer.echo(f"Genotype {genotype!r} not found.", err=True)
            raise typer.Exit(1)
        result = create_vial(
            session,
            genotype_id=geno.id,
            owner_id=owner_id,
            notes=notes,
        )
    typer.echo(
        f"Created vial id={result.vial.id} print_code={result.vial.print_code} "
        f"(genotype: {geno.name})\n  label: {result.label_path}"
    )


@vial_app.command("flip")
def vial_flip_cmd(
    print_code: str = typer.Argument(..., help="Print code of the vial to flip."),
    owner_id: int | None = typer.Option(None, "--owner-id"),
    notes: str | None = typer.Option(None, "--notes"),
) -> None:
    """Decommission a vial and create its successor."""
    with Session(engine) as session:
        try:
            result = flip_vial(
                session,
                old_print_code=print_code.upper(),
                owner_id=owner_id,
                notes=notes,
            )
        except VialNotFoundError as e:
            typer.echo(str(e), err=True)
            raise typer.Exit(1) from e
        except WorkflowError as e:
            typer.echo(str(e), err=True)
            raise typer.Exit(2) from e
    typer.echo(
        f"Flipped {print_code} -> {result.vial.print_code} (id={result.vial.id})\n"
        f"  label: {result.label_path}"
    )


if __name__ == "__main__":
    app()
