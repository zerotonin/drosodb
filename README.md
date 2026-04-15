# DDB — Drosophila Vial Tracking System

[![CI](https://github.com/zerotonin/drosodb/actions/workflows/ci.yml/badge.svg)](https://github.com/zerotonin/drosodb/actions/workflows/ci.yml)
[![Docs](https://github.com/zerotonin/drosodb/actions/workflows/docs.yml/badge.svg)](https://zerotonin.github.io/drosodb/)
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12-blue.svg)](https://www.python.org/)

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
