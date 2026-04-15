from pathlib import Path

import typer
from sqlmodel import Session, select

from ddb.camera.capture import preview, resolve_role
from ddb.camera.config import load_assignments, save_assignments
from ddb.camera.enumeration import discover_cameras
from ddb.config import settings
from ddb.db import engine, init_db
from ddb.importers.genotype_csv import import_genotypes_csv
from ddb.lineage import export_lineage_csv, lineage_for
from ddb.models import Genotype, Vial
from ddb.scanner import PayloadParseError, lookup_by_payload, parse_payload, scan_loop
from ddb.seed import seed_demo
from ddb.workflows import (
    VialNotFoundError,
    WorkflowError,
    create_vial,
    decommission_vial,
    flip_vial,
)

app = typer.Typer(help="DDB — Drosophila Vial Tracking System")
camera_app = typer.Typer(help="Detect, assign, and preview cameras.")
vial_app = typer.Typer(help="Vial workflows: create, flip.")
genotype_app = typer.Typer(help="Inspect known genotypes.")
app.add_typer(camera_app, name="camera")
app.add_typer(vial_app, name="vial")
app.add_typer(genotype_app, name="genotype")


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


@vial_app.command("decommission")
def vial_decommission_cmd(
    print_code: str = typer.Argument(..., help="Print code of the vial."),
    reason: str | None = typer.Option(None, "--reason"),
) -> None:
    """Mark a vial as end-of-life with no successor."""
    with Session(engine) as session:
        try:
            v = decommission_vial(session, print_code=print_code.upper(), reason=reason)
        except VialNotFoundError as e:
            typer.echo(str(e), err=True)
            raise typer.Exit(1) from e
    typer.echo(f"Decommissioned vial id={v.id} print_code={v.print_code}")


@vial_app.command("lineage")
def vial_lineage_cmd(
    print_code: str = typer.Argument(..., help="Print code of any vial in the lineage."),
    csv_out: Path | None = typer.Option(None, "--csv", help="Write full lineage to this CSV file."),
) -> None:
    """Show the full lineage (flip + cross ancestors and descendants) for a vial."""
    with Session(engine) as session:
        vial = session.exec(select(Vial).where(Vial.print_code == print_code.upper())).first()
        if vial is None:
            typer.echo(f"No vial with print_code={print_code!r}", err=True)
            raise typer.Exit(1)
        rows = lineage_for(session, vial.id)
        if csv_out is not None:
            path = export_lineage_csv(session, vial.id, csv_out)
            typer.echo(f"Wrote {len(rows)} rows to {path}")
            return

    typer.echo(f"{'GEN':>3}  {'CODE':<6}  {'ACT':<3}  {'GENO':<18}  {'OWNER':<10}  FLIP_FROM")
    for r in rows:
        act = "Y" if r.is_active else "-"
        typer.echo(
            f"{r.generation:>3}  {r.print_code:<6}  {act:<3}  "
            f"{r.genotype_name[:18]:<18}  {(r.owner_username or '-'):<10}  "
            f"{(r.flipped_from_print_code or '-')}"
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


@genotype_app.command("list")
def genotype_list_cmd(
    search: str | None = typer.Option(
        None, "--search", "-s", help="Case-insensitive substring filter."
    ),
) -> None:
    """Show known genotypes (id, name, wildtype flag, donor strain id)."""
    with Session(engine) as session:
        stmt = select(Genotype).order_by(Genotype.id)
        rows = session.exec(stmt).all()
    if search:
        needle = search.lower()
        rows = [g for g in rows if needle in g.name.lower()]
    if not rows:
        typer.echo("No genotypes match." if search else "No genotypes in DB.")
        return
    typer.echo(f"{'ID':>3}  {'WT':<2}  {'DONOR#':<8}  NAME")
    for g in rows:
        wt = "Y" if g.is_wildtype else "-"
        typer.echo(f"{g.id:>3}  {wt:<2}  {(g.donor_strain_id or '-'):<8}  {g.name}")


@app.command("seed-demo")
def seed_demo_cmd(
    csv_path: Path = typer.Option(
        Path("docs/FlyStockTable.csv"), "--csv", help="Genotype CSV source."
    ),
) -> None:
    """Populate an empty DB with Star Trek medical officers + demo vials."""
    with Session(engine) as session:
        report = seed_demo(session, csv_path=csv_path)
    typer.echo(
        f"seeded: genotypes={report.genotypes_present} "
        f"users+={report.users_created} units+={report.units_created} "
        f"vials={report.vials_created} flipped={report.vials_flipped} "
        f"decommissioned={report.vials_decommissioned}"
    )


@app.command("scan")
def scan_cmd(
    role: str = typer.Option("back", "--role", help="Camera role to use."),
    once: bool = typer.Option(False, "--once", help="Exit after the first scan."),
    no_preview: bool = typer.Option(False, "--no-preview", help="Headless mode."),
) -> None:
    """Live-scan QR labels and print the vial each one refers to."""
    stop_after = 1 if once else None
    for raw in scan_loop(role, show_preview=not no_preview, stop_after=stop_after):
        try:
            parsed = parse_payload(raw)
        except PayloadParseError as e:
            typer.echo(f"[skip] {e}", err=True)
            continue

        with Session(engine) as session:
            result = lookup_by_payload(session, parsed)

        if result is None:
            typer.echo(f"[miss] vial_id={parsed.entity_id} not in DB (payload={raw})")
            continue

        v = result.vial
        tag = "OK" if result.is_fully_consistent else "WARN"
        typer.echo(
            f"[{tag}] vial #{v.id} pc={v.print_code} "
            f"active={v.is_active} genotype_id={v.genotype_id}"
        )
        if not result.print_code_matches:
            typer.echo(
                f"    print_code mismatch: label says {parsed.print_code}, "
                f"DB has {v.print_code} (label likely stale)"
            )
        if not result.database_matches:
            typer.echo(
                f"    database_id mismatch: label says {parsed.database_id}, "
                f"this install is {settings.database_id}"
            )


if __name__ == "__main__":
    app()
