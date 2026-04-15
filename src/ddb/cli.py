from pathlib import Path

import typer
from sqlmodel import Session

from ddb.camera.capture import preview, resolve_role
from ddb.camera.config import load_assignments, save_assignments
from ddb.camera.enumeration import discover_cameras
from ddb.db import engine, init_db
from ddb.importers.genotype_csv import import_genotypes_csv

app = typer.Typer(help="DDB — Drosophila Vial Tracking System")
camera_app = typer.Typer(help="Detect, assign, and preview cameras.")
app.add_typer(camera_app, name="camera")


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


if __name__ == "__main__":
    app()
