"""ReportLab renderer for the biosafety PDF.

Takes a `BiosecReport` (built by `data.gather_biosec_report`) and
writes a print-ready PDF. Layout is deliberately plain — no logos,
no colours that wouldn't photocopy, no rotated text — because the
intended use is to staple a copy into an institutional binder.

The renderer is the only place reportlab is imported, so callers that
just want the numbers (tests, ad-hoc scripts, the GUI's status bar)
don't pull a heavy dependency.
"""

from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from .data import BiosecReport, GenotypeRow, OwnerCount


def render_biosec_pdf(report: BiosecReport, out_path: Path) -> Path:
    """Write `report` to `out_path` as a PDF; return the path."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    doc = SimpleDocTemplate(
        str(out_path),
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title="DDB biosafety report",
        author="DDB",
    )
    story = _build_story(report)
    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return out_path


# ---------------------------------------------------------------------------
# Story assembly
# ---------------------------------------------------------------------------


def _build_story(report: BiosecReport) -> list:
    styles = _styles()
    story: list = []

    story.append(Paragraph("Drosophila stock — biosafety report", styles["TitleBig"]))
    story.append(Spacer(1, 4 * mm))
    story.append(_meta_block(report, styles))
    story.append(Spacer(1, 6 * mm))

    story.append(Paragraph("Snapshot at end date", styles["H2"]))
    story.append(_snapshot_table(report))
    story.append(Spacer(1, 5 * mm))

    story.append(Paragraph("Activity during the reporting period", styles["H2"]))
    story.append(_period_table(report))
    story.append(Spacer(1, 5 * mm))

    if report.owner_breakdown:
        story.append(Paragraph("Active vials by owner (at end date)", styles["H2"]))
        story.append(_owner_table(report.owner_breakdown))
        story.append(Spacer(1, 5 * mm))

    story.append(Paragraph("Signature", styles["H2"]))
    story.append(_signature_block(styles))

    if report.genotype_listing:
        story.append(PageBreak())
        story.append(
            Paragraph(
                f"Annex: in-stock genotype listing ({len(report.genotype_listing)} entries)",
                styles["H2"],
            )
        )
        story.append(Spacer(1, 2 * mm))
        story.append(_genotype_listing_table(report.genotype_listing))

    return story


# ---------------------------------------------------------------------------
# Building blocks
# ---------------------------------------------------------------------------


def _meta_block(report: BiosecReport, styles) -> Table:
    scope = report.org_unit_name or "Whole database (all org units)"
    if report.owner_username:
        scope += f" — owner filter: {report.owner_username}"
    period = f"{report.period_from.strftime('%Y-%m-%d')} to {report.period_to.strftime('%Y-%m-%d')}"
    generated = report.generated_at.strftime("%Y-%m-%d %H:%M UTC")
    rows = [
        ["Scope:", Paragraph(scope, styles["Body"])],
        ["Reporting period:", Paragraph(period, styles["Body"])],
        ["Generated:", Paragraph(generated, styles["Body"])],
    ]
    t = Table(rows, colWidths=[38 * mm, None])
    t.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 10),
            ]
        )
    )
    return t


def _snapshot_table(report: BiosecReport) -> Table:
    s = report.snapshot
    data = [
        ["Metric", "Count"],
        ["Active vials", str(s.active_vials)],
        ["Decommissioned vials (cumulative)", str(s.decommissioned_vials)],
        ["In-stock genotypes", str(s.in_stock_genotypes)],
        ["Dropped genotypes", str(s.dropped_genotypes)],
        ["Total vials on record", str(s.total_vials)],
        ["Total genotypes on record", str(s.total_genotypes)],
    ]
    return _two_col_table(data)


def _period_table(report: BiosecReport) -> Table:
    p = report.period
    data = [
        ["Event", "Count"],
        ["New vials created", str(p.new_vials)],
        ["Vials flipped (1-to-1 + multiply)", str(p.vials_flipped)],
        ["Vials decommissioned (standalone)", str(p.vials_decommissioned_standalone)],
        ["Vials decommissioned (via flip / multiply)", str(p.vials_decommissioned_via_flip)],
        ["Vials reactivated", str(p.vials_reactivated)],
        ["New genotypes added", str(p.new_genotypes)],
        ["Genotypes dropped from stock", str(p.genotypes_dropped)],
        ["Genotypes reactivated in stock", str(p.genotypes_reactivated)],
    ]
    return _two_col_table(data)


def _owner_table(owners: list[OwnerCount]) -> Table:
    rows: list[list[str]] = [["Owner", "Full name", "Active vials"]]
    for o in owners:
        rows.append(
            [
                o.owner_username or "—",
                o.owner_full_name or "—",
                str(o.active_vials),
            ]
        )
    t = Table(rows, colWidths=[40 * mm, None, 25 * mm], repeatRows=1)
    t.setStyle(_basic_table_style(header=True, last_col_right=True))
    return t


def _genotype_listing_table(rows: list[GenotypeRow]) -> Table:
    styles = _styles()
    cell = styles["Cell"]
    header = [
        Paragraph("<b>Name</b>", cell),
        Paragraph("<b>Notation (X ; II ; III)</b>", cell),
        Paragraph("<b>Donor #</b>", cell),
        Paragraph("<b>Phenotype / notes</b>", cell),
        Paragraph("<b>WT</b>", cell),
        Paragraph("<b>Vials</b>", cell),
    ]
    data: list[list] = [header]
    for g in rows:
        extras: list[str] = []
        if g.phenotype:
            extras.append(g.phenotype.strip())
        if g.notes:
            extras.append(g.notes.strip())
        extras_text = " — ".join(extras) if extras else "—"
        data.append(
            [
                Paragraph(_escape(g.name), cell),
                Paragraph(_escape(g.notation), cell),
                Paragraph(_escape(g.donor_strain_id or "—"), cell),
                Paragraph(_escape(extras_text), cell),
                "Y" if g.is_wildtype else "—",
                str(g.active_vial_count),
            ]
        )
    t = Table(
        data,
        colWidths=[28 * mm, 48 * mm, 18 * mm, None, 9 * mm, 14 * mm],
        repeatRows=1,
    )
    t.setStyle(_basic_table_style(header=True, last_col_right=True))
    return t


def _escape(s: str) -> str:
    """Escape reportlab Paragraph markup chars; chromosome notation
    contains `&`, `<`, `>` from FlyBase strings."""
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _signature_block(styles) -> Table:
    rows = [
        ["Stock keeper:", "___________________________", "Date:", "___________"],
        ["Lab head:", "___________________________", "Date:", "___________"],
    ]
    t = Table(rows, colWidths=[28 * mm, 70 * mm, 18 * mm, 38 * mm])
    t.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 10),
            ]
        )
    )
    return t


def _two_col_table(data: list[list[str]]) -> Table:
    t = Table(data, colWidths=[110 * mm, 25 * mm], repeatRows=1)
    t.setStyle(_basic_table_style(header=True, last_col_right=True))
    return t


def _basic_table_style(*, header: bool, last_col_right: bool) -> TableStyle:
    commands: list = [
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
    ]
    if header:
        commands.extend(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ]
        )
    if last_col_right:
        commands.append(("ALIGN", (-1, 0), (-1, -1), "RIGHT"))
    return TableStyle(commands)


# ---------------------------------------------------------------------------
# Footer (page number + traceability)
# ---------------------------------------------------------------------------


def _footer(canvas, doc) -> None:
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.grey)
    canvas.drawString(
        18 * mm,
        10 * mm,
        "DDB biosafety report — Drosophila stock book",
    )
    canvas.drawRightString(
        doc.pagesize[0] - 18 * mm,
        10 * mm,
        f"Page {doc.page}",
    )
    canvas.restoreState()


def _styles():
    base = getSampleStyleSheet()
    return {
        "TitleBig": ParagraphStyle(
            "TitleBig", parent=base["Title"], fontSize=18, leading=22, spaceAfter=4
        ),
        "H2": ParagraphStyle(
            "H2",
            parent=base["Heading2"],
            fontSize=12,
            leading=14,
            spaceBefore=4,
            spaceAfter=4,
        ),
        "Body": ParagraphStyle("Body", parent=base["BodyText"], fontSize=10, leading=12),
        "Cell": ParagraphStyle("Cell", parent=base["BodyText"], fontSize=8, leading=10),
    }
