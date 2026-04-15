# DDB — Drosophila Vial Tracking System

Single-user lab system for tracking *Drosophila* vials with QR-coded labels,
full lineage, and an auditable history.

See `docs/droso_db_project_extended.md` for the full spec.

## Quickstart (dev)

```bash
conda env create -f environment.yml      # one-time
conda activate ddb
pip install -e .                          # editable install of the ddb package
pytest
```

## Layout

```
src/ddb/        # package
  models/       # SQLModel ORM models
  db.py         # engine + session
  config.py     # settings
  cli.py        # typer CLI
tests/
alembic/        # migrations
docs/           # design docs
```
