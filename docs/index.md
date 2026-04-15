# DDB — Drosophila Vial Tracking System

Python-based lab tool for managing *Drosophila* vials: QR-coded identity,
automated label printing (17×54mm DK-11204), full lineage, SQLite or
PostgreSQL backend, scan-driven workflows, and an auditable history.

## Status

- Data model, Alembic migrations, and `FlyStockTable.csv` importer ready.
- Print-code, QR payload, and label renderer implemented (see
  [sample label](sample_label.png)).
- `create_vial` + `flip_vial` workflows with single-transaction audit.
- Camera enumeration and CLI preview working; GUI is the next major slice.

See the [core spec](droso_db_project.md) and the
[extended spec](droso_db_project_extended.md) for design notes.

## Quickstart

```bash
conda env create -f environment.yml
conda activate ddb
pip install -e .
alembic upgrade head
ddb import-genotypes docs/FlyStockTable.csv
ddb vial create Canton-S
```
