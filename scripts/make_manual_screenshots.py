#!/usr/bin/env python
# ╔══════════════════════════════════════════════════════════════════╗
# ║  DDB — make_manual_screenshots                                   ║
# ║  « fresh pictures for the manual, from fake flies »              ║
# ╠══════════════════════════════════════════════════════════════════╣
# ║  Renders every tab and the key dialogs offscreen into            ║
# ║  docs/manual/img/, using a throwaway demo database. It refuses   ║
# ║  to run unless DDB is pointed at that database, so real lab      ║
# ║  data can never end up in a screenshot.                          ║
# ╚══════════════════════════════════════════════════════════════════╝
"""Regenerate the user-manual screenshots.

Run after any change that alters what a tab or a pictured dialog looks
like, then commit the PNGs together with the code and manual edits::

    python scripts/make_manual_screenshots.py
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = REPO_ROOT / "docs" / "manual" / "img"
WINDOW_SIZE = (1180, 720)
DIALOG_SIZE = (900, 560)
WIDE_DIALOGS = {"dialog_audit_trail", "dialog_genealogy"}  # others keep their natural size
DEMO_USERNAME = "demo"

# ┌────────────────────────────────────────────────────────────┐
# │ Sandbox  « throwaway DB, demo user, no printer »           │
# └────────────────────────────────────────────────────────────┘


def sandbox_environment(workdir: Path) -> dict[str, str]:
    """Environment that points DDB at ``workdir`` and nothing else.

    Must be applied before ``ddb`` is imported: settings and the engine
    are built at import time. Every setting a picture depends on is pinned
    here, because environment variables beat the clone's ``.env`` and the
    pictures must not change with whoever runs the script.
    """
    return {
        "QT_QPA_PLATFORM": "offscreen",
        "DDB_DATABASE_URL": f"sqlite:///{workdir / 'demo.sqlite3'}",
        "DDB_DATA_DIR": str(workdir / "data"),
        "DDB_CAMERA_CONFIG": str(workdir / "cameras.json"),
        "DDB_ACTOR_USERNAME_OVERRIDE": DEMO_USERNAME,
        "DDB_DEFAULT_OWNER_USERNAME": "stockkeeper",
        "DDB_PRINTER_ENABLED": "0",
        "DDB_PRINTER_BACKEND": "bluetooth",
        "DDB_PRINTER_BLUETOOTH_MAC": "00:00:00:00:00:00",
        "DDB_GUI_UPDATE_CHECK": "0",
        "DDB_GUI_DEBUG": "0",
        "DDB_GUI_FONT_SCALE": "1.0",
        "DDB_SUPPRESS_FLIP_CONFIRM": "0",
        "DDB_SUPPRESS_DECOMMISSION_CONFIRM": "0",
        "DDB_DEFAULT_CAMERA_ROLE": "front",
        # Enabled shows Genotypes → Import…; "manual" keeps the start-up
        # catalog prompt (a blocking dialog) from firing.
        "DDB_FLYBASE_ENABLED": "1",
        "DDB_FLYBASE_REFRESH_MODE": "manual",
    }


def assert_sandboxed(workdir: Path) -> None:
    from ddb.config import settings
    from ddb.db import engine

    expected = str(workdir / "demo.sqlite3")
    if str(engine.url.database) != expected or not str(settings.database_url).endswith(expected):
        sys.exit(f"refusing to run: DDB is using {engine.url}, not the demo database")


# ┌────────────────────────────────────────────────────────────┐
# │ Demo data  « a small, believable fly room »                │
# └────────────────────────────────────────────────────────────┘

DEMO_GENOTYPES: list[dict[str, object]] = [
    {"name": "Canton-S", "is_wildtype": True, "phenotype": "wild type"},
    {"name": "w1118", "chromosome_x": "w[1118]", "phenotype": "white eyes"},
    {
        "name": "elav-GAL4 > UAS-mCD8::GFP",
        "chromosome_x": "P{GawB}elav[C155]",
        "chromosome_2": "P{UAS-mCD8::GFP.L}LL5",
        "phenotype": "GFP in all neurons",
    },
    {"name": "nlg4 KO70", "chromosome_3": "nlg4[KO70]", "phenotype": "reduced sleep"},
    {"name": "Or83b-GAL4; UAS-TrpA1", "phenotype": "heat-activated olfactory neurons"},
]


def seed_demo_data() -> dict[str, str]:
    """Fill the demo DB; return print codes of vials worth showing."""
    from sqlmodel import Session, SQLModel

    from ddb.config import settings
    from ddb.db import engine
    from ddb.models import Genotype, User
    from ddb.workflows import create_vial, decommission_vial, flip_vial, multiply_vial

    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        demo = User(username=DEMO_USERNAME, full_name="Demo User")
        keeper = User(username=settings.default_owner_username, full_name="Stock keeper")
        session.add_all([demo, keeper])
        session.commit()
        genotypes = [Genotype(**fields, created_by_id=demo.id) for fields in DEMO_GENOTYPES]
        session.add_all(genotypes)
        session.commit()
        actor = {"actor_id": demo.id}
        owned = {**actor, "owner_id": keeper.id}

        roots = [create_vial(session, genotype_id=g.id, **owned).vial for g in genotypes]
        create_vial(session, genotype_id=genotypes[1].id, **owned)
        flipped = flip_vial(session, old_print_code=roots[3].print_code, **actor).vial
        multiply_vial(session, old_print_code=flipped.print_code, count=3, **actor)
        active = flip_vial(session, old_print_code=roots[0].print_code, **actor).vial
        flip_vial(session, old_print_code=roots[2].print_code, **actor)
        spare = create_vial(session, genotype_id=genotypes[4].id, **owned).vial
        decommission_vial(session, print_code=spare.print_code, reason="Mites", **actor)
        return {"scan": active.print_code, "family_id": str(flipped.id)}


# ┌────────────────────────────────────────────────────────────┐
# │ Capture  « tabs, then dialogs »                            │
# └────────────────────────────────────────────────────────────┘


def capture(out_dir: Path, demo: dict[str, str]) -> list[Path]:
    from PySide6.QtWidgets import QApplication

    from ddb.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.resize(*WINDOW_SIZE)
    window.show()
    settle(app)
    written: list[Path] = []

    def save(widget, name: str) -> None:  # noqa: ANN001
        settle(app)
        path = out_dir / f"{name}.png"
        widget.grab().save(str(path))
        written.append(path)

    window.scan_tab.code_edit.setText(demo["scan"])
    window.scan_tab.lookup_btn.click()
    for index, name in enumerate(["tab_scan", "tab_reports", "tab_genotypes"]):
        window.tabs.setCurrentIndex(index)
        save(window, name)
    window.tabs.setCurrentIndex(3)
    save(window.settings_tab, "tab_settings")  # whole tab, not the scrolled view

    for name, build in dialog_builders(int(demo["family_id"])).items():
        dialog = build()
        if name in WIDE_DIALOGS:
            dialog.resize(*DIALOG_SIZE)
        else:
            dialog.adjustSize()
        dialog.show()
        save(dialog, name)
        dialog.close()

    window.close()
    settle(app)
    return written


def dialog_builders(vial_id: int) -> dict[str, Callable[[], object]]:
    from sqlmodel import Session

    from ddb.db import engine
    from ddb.gui.dialogs.audit_trail import AuditTrailDialog
    from ddb.gui.dialogs.create_vial import CreateVialDialog
    from ddb.gui.dialogs.flip_genotype import FlipGenotypeDialog
    from ddb.gui.dialogs.lineage_chart import LineageDialog
    from ddb.gui.dialogs.printer_reconnect import PrinterReconnectDialog
    from ddb.gui.printer_status import PrinterState, PrinterStatus
    from ddb.lineage import lineage_graph
    from ddb.reports import vial_detail
    from ddb.vial_history import vial_audit_trail

    def audit() -> AuditTrailDialog:
        with Session(engine) as session:
            return AuditTrailDialog(
                vial_detail(session, vial_id), vial_audit_trail(session, vial_id)
            )

    def genealogy() -> LineageDialog:
        with Session(engine) as session:
            return LineageDialog(lineage_graph(session, vial_id))

    def printer() -> PrinterReconnectDialog:
        status = PrinterStatus(PrinterState.UNREACHABLE, "Bluetooth connect timed out")
        return PrinterReconnectDialog(status)

    return {
        "dialog_new_vial": CreateVialDialog,
        "dialog_flip_all": FlipGenotypeDialog,
        "dialog_printer": printer,
        "dialog_audit_trail": audit,
        "dialog_genealogy": genealogy,
    }


def settle(app) -> None:  # noqa: ANN001
    for _ in range(5):
        app.processEvents()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="output folder")
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="ddb-manual-") as tmp:
        workdir = Path(tmp)
        os.environ.update(sandbox_environment(workdir))
        assert_sandboxed(workdir)
        demo = seed_demo_data()
        for path in capture(args.out, demo):
            print(path.relative_to(REPO_ROOT) if path.is_relative_to(REPO_ROOT) else path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
