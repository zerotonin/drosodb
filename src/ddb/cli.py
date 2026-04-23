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
from ddb.reports import search_vials, vial_detail
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
printer_app = typer.Typer(help="Brother QL-series label printer.")
app.add_typer(camera_app, name="camera")
app.add_typer(vial_app, name="vial")
app.add_typer(genotype_app, name="genotype")
app.add_typer(printer_app, name="printer")


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


@vial_app.command("search")
def vial_search_cmd(
    active_only: bool = typer.Option(False, "--active", help="Only active vials."),
    inactive_only: bool = typer.Option(False, "--inactive", help="Only decommissioned vials."),
    genotype: str | None = typer.Option(
        None, "--genotype", help="Substring match on genotype name."
    ),
    gene: str | None = typer.Option(
        None, "--gene", help="Substring across chromosomes + phenotype."
    ),
    owner: str | None = typer.Option(None, "--owner", help="Exact owner username."),
    unit: str | None = typer.Option(None, "--unit", help="Exact org-unit name."),
    donor: str | None = typer.Option(None, "--donor", help="Exact donor name."),
    donor_strain_id: str | None = typer.Option(None, "--donor-strain-id"),
    generation: int | None = typer.Option(None, "--generation"),
    limit: int = typer.Option(50, "--limit"),
) -> None:
    """Search vials across joined genotype/user/donor/unit tables."""
    if active_only and inactive_only:
        typer.echo("--active and --inactive are mutually exclusive", err=True)
        raise typer.Exit(2)
    active: bool | None = True if active_only else (False if inactive_only else None)

    with Session(engine) as session:
        rows = search_vials(
            session,
            active=active,
            genotype_name_contains=genotype,
            gene_contains=gene,
            owner_username=owner,
            org_unit_name=unit,
            donor_name=donor,
            donor_strain_id=donor_strain_id,
            generation=generation,
            limit=limit,
        )

    if not rows:
        typer.echo("No vials match.")
        return
    typer.echo(
        f"{'CODE':<6} {'GEN':>3} {'ACT':<3} {'OWNER':<10} {'GENO':<18} {'DONOR':<18} CREATED"
    )
    for r in rows:
        act = "Y" if r.is_active else "-"
        typer.echo(
            f"{r.print_code:<6} {r.generation:>3} {act:<3} "
            f"{(r.owner_username or '-'):<10} {r.genotype_name[:18]:<18} "
            f"{(r.donor_name or '-')[:18]:<18} {r.created_at.date()}"
        )
    typer.echo(f"({len(rows)} row{'s' if len(rows) != 1 else ''})")


@vial_app.command("show")
def vial_show_cmd(
    print_code: str = typer.Argument(..., help="Print code of the vial."),
) -> None:
    """Print a full detail record (the scan-panel view) for one vial."""
    with Session(engine) as session:
        v = session.exec(select(Vial).where(Vial.print_code == print_code.upper())).first()
        if v is None:
            typer.echo(f"No vial with print_code={print_code!r}", err=True)
            raise typer.Exit(1)
        detail = vial_detail(session, v.id)

    assert detail is not None
    r = detail.row
    typer.echo(f"  print code   {r.print_code}")
    typer.echo(f"  status       {'ACTIVE' if r.is_active else 'decommissioned'}")
    typer.echo(f"  generation   {r.generation}")
    typer.echo(f"  genotype     {r.genotype_name}  (id={r.genotype_id})")
    if r.phenotype:
        typer.echo(f"  phenotype    {r.phenotype}")
    typer.echo(f"  wildtype     {'yes' if r.is_wildtype else 'no'}")
    typer.echo(f"  owner        {r.owner_username or '-'} ({r.owner_full_name or '-'})")
    typer.echo(f"  org unit     {r.org_unit_name or '-'}")
    typer.echo(f"  donor        {r.donor_name or '-'}  strain#{r.donor_strain_id or '-'}")
    typer.echo(f"  created      {r.created_at.isoformat(timespec='seconds')}")
    if r.decommissioned_at:
        typer.echo(f"  decomm'd     {r.decommissioned_at.isoformat(timespec='seconds')}")
    if detail.parent_flip_print_code:
        typer.echo(f"  flipped from {detail.parent_flip_print_code}")
    if detail.parent_cross_print_codes:
        typer.echo(f"  cross parents {', '.join(detail.parent_cross_print_codes)}")
    if detail.child_flip_print_codes:
        typer.echo(f"  flipped to   {', '.join(detail.child_flip_print_codes)}")
    if detail.child_cross_print_codes:
        typer.echo(f"  cross kids   {', '.join(detail.child_cross_print_codes)}")
    if r.notes:
        typer.echo(f"  notes        {r.notes}")
    if detail.audit:
        typer.echo("  audit:")
        for a in detail.audit:
            typer.echo(
                f"    {a.created_at.isoformat(timespec='seconds')}  "
                f"{a.action:<14} by {a.actor_username or '-'}"
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


@printer_app.command("test")
def printer_test_cmd(
    label_png: Path = typer.Option(
        Path("docs/sample_label.png"),
        "--png",
        help="PNG to print. Defaults to the shipped sample_label.png.",
    ),
    timeout: float = typer.Option(30.0, "--timeout"),
) -> None:
    """Print a single test label through the configured backend."""
    from ddb.printing.service import PrinterError, print_png

    if not label_png.exists():
        typer.echo(f"PNG not found: {label_png}", err=True)
        raise typer.Exit(1)
    try:
        result = print_png(label_png.read_bytes(), timeout=timeout)
    except PrinterError as e:
        typer.echo(f"Printer error: {e}", err=True)
        raise typer.Exit(2) from e
    typer.echo(result.summary())


@printer_app.command("label")
def printer_label_cmd(
    print_code: str = typer.Argument(..., help="Print code of the vial."),
    timeout: float = typer.Option(30.0, "--timeout"),
) -> None:
    """Re-print the stored label PNG for an existing vial."""
    from ddb.printing.service import PrinterError, print_png

    with Session(engine) as session:
        v = session.exec(select(Vial).where(Vial.print_code == print_code.upper())).first()
        if v is None:
            typer.echo(f"No vial with print_code={print_code!r}", err=True)
            raise typer.Exit(1)
    label_path = settings.data_dir / "labels" / f"{v.print_code}.png"
    if not label_path.exists():
        typer.echo(
            f"Label PNG missing: {label_path}. Re-render with `ddb vial create/flip`.",
            err=True,
        )
        raise typer.Exit(1)
    try:
        result = print_png(label_path.read_bytes(), timeout=timeout)
    except PrinterError as e:
        typer.echo(f"Printer error: {e}", err=True)
        raise typer.Exit(2) from e
    typer.echo(result.summary())


@printer_app.command("status")
def printer_status_cmd(timeout: float = typer.Option(10.0, "--timeout")) -> None:
    """Send ESC i S to the printer and decode the reply.

    Useful for verifying BT connectivity and seeing what media is loaded
    without committing a print job.
    """
    from ddb.printing.service import PrinterError, get_backend
    from ddb.printing.status import decode_status_blocks

    try:
        backend = get_backend()
    except PrinterError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(2) from e

    # ESC @ (init) + ESC i S (status request). No raster, no print.
    query = b"\x00" * 200 + b"\x1b\x40" + b"\x1b\x69\x53"
    typer.echo(f"Querying {backend.description}…")
    try:
        blocks = backend.send(query, timeout=timeout)
    except (ConnectionError, OSError) as e:
        typer.echo(f"transport error: {e}", err=True)
        raise typer.Exit(3) from e
    # The status-only request also goes through the sidecar's full
    # read-until-terminal path; just decode whatever came back.
    blocks = blocks or decode_status_blocks(b"")
    if not blocks:
        typer.echo("(no status reply received)")
        return
    for b in blocks:
        typer.echo(f"  {b.describe()}")


@app.command("gui")
def gui_cmd() -> None:
    """Launch the PySide6 GUI (requires the [gui] extra / conda env)."""
    try:
        from ddb.gui.app import main as gui_main
    except ModuleNotFoundError as e:
        typer.echo(
            f"PySide6 not installed: {e}\n  pip install -e .[gui]  (or use the conda env)",
            err=True,
        )
        raise typer.Exit(1) from e
    raise typer.Exit(gui_main())


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
