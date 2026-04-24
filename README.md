# DDB — Drosophila Vial Tracking System

[![CI](https://github.com/zerotonin/drosodb/actions/workflows/ci.yml/badge.svg)](https://github.com/zerotonin/drosodb/actions/workflows/ci.yml)
[![Docs](https://github.com/zerotonin/drosodb/actions/workflows/docs.yml/badge.svg)](https://zerotonin.github.io/drosodb/)
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12-blue.svg)](https://www.python.org/)

Single-user lab system for tracking *Drosophila* vials with QR-coded labels,
printer-driven workflow, lineage across flips and crosses, and an auditable
history. Built for a wet-lab scientist who wants the stock book to stay in
sync with the freezer — not for a multi-user SaaS.

## What it does

- **Create vials** with a compact QR label printed on a Brother QL-820NWB
  (17 × 54 mm DK-11204), one click from genotype + owner + org-unit.
- **Scan vials** via USB webcam in the Scan tab; decoded payloads fetch
  the full detail panel (genotype notation, audit trail, lineage).
- **Flip / decommission** vials with audit-logged state transitions; the
  lineage graph keeps parent/child relations traversable.
- **Import genotypes by stock-center ID** (Bloomington, Vienna/VDRC,
  Kyoto/DGGR, NIG-Fly, KDRC, FlyORF, NDSSC) from a locally-cached
  FlyBase catalog — no per-import web calls.
- **Reports** tab: search/filter by genotype, owner, org-unit, donor,
  generation; export lineage as CSV.
- **Auditable**: every mutation writes an `AuditEvent` with actor, action,
  and before/after payload.
- **Typer CLI** mirrors the GUI for scripted workflows (`ddb vial create`,
  `ddb vial flip`, `ddb printer status`, etc.).

## Quickstart

```bash
# one-time
conda env create -f environment.yml
conda activate ddb
pip install -e ".[gui,hardware,dev]"

# initialise the SQLite DB
ddb init-db

# launch the GUI
ddb gui
```

Run the tests to confirm the install:

```bash
pytest     # ~190 tests, ~7 s
```

## Hardware supported

| Device       | Model               | Transport                | Notes                                  |
|--------------|---------------------|--------------------------|----------------------------------------|
| Label printer| Brother QL-820NWB   | Bluetooth RFCOMM (ch 1)  | Also: TCP :9100 (network) or file dump |
| QR camera    | any V4L2 webcam     | OpenCV (libv4l)          | zxing-cpp primary, OpenCV fallback     |
| OS           | Linux 6.x (tested)  | BlueZ 5.72+              | macOS / Windows untested               |

## Configuration

Everything is controlled via `.env` in the project root (or `DDB_*`
environment variables). The most common knobs:

```env
# Printer
DDB_PRINTER_ENABLED=1
DDB_PRINTER_BACKEND=bluetooth       # or "network" or "file"
DDB_PRINTER_BLUETOOTH_MAC=AC:4D:16:EB:B6:44
DDB_PRINTER_AUTO_PRINT=1

# Scanner / camera defaults
DDB_DEFAULT_CAMERA_ROLE=back        # or "front"

# FlyBase genotype-import catalog
DDB_FLYBASE_ENABLED=1
DDB_FLYBASE_REFRESH_MODE=monthly    # "manual", "weekly", "monthly"

# Stock-keeper defaults for the New-Vial dialog
DDB_DEFAULT_ORG_UNIT=Geurten lab stock
DDB_DEFAULT_OWNER_USERNAME=stockkeeper
```

See `src/ddb/config.py` for the full set — every field is overridable
via `DDB_<UPPERCASE_NAME>`.

## Layout

```
src/ddb/
  models/       SQLModel ORM (Vial, Genotype, Donor, OrgUnit, User, AuditEvent, …)
  workflows/    transaction-safe mutations with audit (create/flip/decommission,
                drop/reactivate genotype)
  printing/     raster pipeline + backends (bluetooth / network / file) +
                bt_recovery helpers + _sidecars scripts
  scanner/      QR decode (zxing + OpenCV fallback), payload parser,
                DB lookup by print-code or FBst
  camera/       V4L2 enumeration, role assignment, preview capture
  flybase/      local FlyBase stocks-catalog cache (download, lookup,
                genotype parser, refresh scheduler, collection→donor mapping)
  gui/          PySide6: Scan / Reports / Genotypes / Settings tabs,
                dialogs (CreateVial, CreateGenotype, ImportGenotype,
                PrinterReconnect, FlybaseDownload), shared widgets
                (GenotypeForm, CameraWidget, PrinterStatusLight)
  importers/    FlyStockTable CSV importer
  cli.py        typer CLI entry point
  config.py     pydantic-settings (env-driven)
  db.py         engine + session factory
  labels.py     17×54mm label rendering with QR
  qr.py         compact "DDB:<code>" payload + PNG builder
  lineage.py    vial lineage graph + CSV export
  reports.py    search + detail queries
tests/          ~190 unit + integration tests
alembic/        SQLite migrations
docs/           design docs + operations walkthrough
```

## CLI reference (selected)

```bash
ddb init-db                          # create tables (Alembic)
ddb gui                              # launch the PySide6 desktop app
ddb scan --role back                 # stand-alone scanner loop
ddb seed-demo                        # populate a demo DB

ddb vial create --genotype-id 12 --owner-id 3
ddb vial search --genotype rutabaga --active
ddb vial show <PRINT_CODE>
ddb vial flip <PRINT_CODE>           # creates a child vial, audits both
ddb vial decommission <PRINT_CODE> --reason contaminated
ddb vial lineage <PRINT_CODE> --csv lineage.csv

ddb import-genotypes path/to/FlyStockTable.csv
ddb printer status                   # ESC i S probe via configured backend
ddb printer print-png path/to/label.png
```

Run `ddb --help` or `ddb <subcommand> --help` for the full set.

## Further reading

- `docs/droso_db_project_extended.md` — full design spec.
- `docs/operations.md` — manual reprint + backup walkthrough.
- `docs/index.md` — docs landing page (published at
  [zerotonin.github.io/drosodb](https://zerotonin.github.io/drosodb/)).
