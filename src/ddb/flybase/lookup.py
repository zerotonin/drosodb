"""Query the local FlyBase stocks TSV.

The file is small enough (~180k rows, 18 MB unzipped) that we parse the
whole thing into an in-memory dict on first access and keep it for the
session. Subsequent lookups are O(1) by `(collection_lower, stock_number_lower)`
or by `fbst_lower`. The module-level cache invalidates automatically
when the TSV's mtime changes (i.e. after a refresh download).
"""

from __future__ import annotations

import csv
import gzip
import threading
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CatalogRecord:
    """One row from the FlyBase stocks TSV, field-normalised."""

    fbst: str
    collection: str
    stock_type: str  # "living stock ; FBsv:..." — raw, first segment is the useful bit
    species: str  # e.g. "Dmel"
    genotype: str  # semicolon-delimited per the parser module
    description: str
    stock_number: str  # BDSC / Kyoto integer, VDRC "v…" prefix

    @property
    def is_living_stock(self) -> bool:
        """True if the row's stock_type begins with 'living stock'.

        FlyBase occasionally emits 'lost stock' rows for records where
        the strain is gone from its collection but the FBst ID stays
        reserved. We want the default import UX to hide those unless
        the user specifically asks.
        """
        return self.stock_type.strip().lower().startswith("living stock")


# ----------------------------------------------------------------------
# Indexing
# ----------------------------------------------------------------------


_IndexKey = tuple[str, str]  # (collection_lower, stock_number_lower)


@dataclass
class _Index:
    by_collection_number: dict[_IndexKey, CatalogRecord]
    by_fbst: dict[str, CatalogRecord]
    collection_counts: dict[str, int]
    source_path: Path
    source_mtime_ns: int


_cache: _Index | None = None
_cache_lock = threading.Lock()


def _normalise_stock_number(raw: str) -> str:
    """Match user-typed IDs tolerantly: strip surrounding whitespace and
    a leading `v`/`V` for VDRC (users type either `10004` or `v10004`)."""
    s = raw.strip().lower()
    if s.startswith("v") and s[1:].isdigit():
        return s[1:]
    return s


def _build_index(tsv_gz_path: Path) -> _Index:
    by_cn: dict[_IndexKey, CatalogRecord] = {}
    by_fbst: dict[str, CatalogRecord] = {}
    counts: dict[str, int] = {}

    with gzip.open(tsv_gz_path, mode="rt", encoding="utf-8", newline="") as fh:
        reader = csv.reader(fh, delimiter="\t")
        header = next(reader, None)
        if header is None:
            raise ValueError(f"{tsv_gz_path}: empty file")
        # The file column order is stable across releases: FBst,
        # collection_short_name, stock_type_cv, species, FB_genotype,
        # description, stock_number. We tolerate extra columns at the
        # end (FlyBase occasionally appends fields) by indexing the
        # first seven only.
        expected = [
            "FBst",
            "collection_short_name",
            "stock_type_cv",
            "species",
            "FB_genotype",
            "description",
            "stock_number",
        ]
        if header[: len(expected)] != expected:
            raise ValueError(
                f"{tsv_gz_path}: unexpected header {header!r}; expected prefix {expected!r}"
            )

        for row in reader:
            if len(row) < 7:
                continue
            rec = CatalogRecord(
                fbst=row[0].strip(),
                collection=row[1].strip(),
                stock_type=row[2].strip(),
                species=row[3].strip(),
                genotype=row[4].strip(),
                description=row[5].strip(),
                stock_number=row[6].strip(),
            )
            if not rec.collection or not rec.stock_number:
                continue
            key = (rec.collection.lower(), _normalise_stock_number(rec.stock_number))
            by_cn[key] = rec
            if rec.fbst:
                by_fbst[rec.fbst.lower()] = rec
            counts[rec.collection] = counts.get(rec.collection, 0) + 1

    return _Index(
        by_collection_number=by_cn,
        by_fbst=by_fbst,
        collection_counts=counts,
        source_path=tsv_gz_path,
        source_mtime_ns=tsv_gz_path.stat().st_mtime_ns,
    )


def _get_index(tsv_gz_path: Path) -> _Index:
    """Return the cached index, rebuilding if the file changed on disk."""
    global _cache
    stat_ns = tsv_gz_path.stat().st_mtime_ns
    with _cache_lock:
        if _cache is None or _cache.source_path != tsv_gz_path or _cache.source_mtime_ns != stat_ns:
            _cache = _build_index(tsv_gz_path)
        return _cache


def reset_cache() -> None:
    """Force the next call to rebuild the index — mostly for tests."""
    global _cache
    with _cache_lock:
        _cache = None


# ----------------------------------------------------------------------
# Public lookup API
# ----------------------------------------------------------------------


def find_record(tsv_gz_path: Path, collection: str, stock_number: str) -> CatalogRecord | None:
    """Look up a stock by its collection short-name + stock number.

    `collection` and `stock_number` are matched case-insensitively;
    VDRC's `v` prefix is accepted or omitted.
    """
    index = _get_index(tsv_gz_path)
    key = (collection.strip().lower(), _normalise_stock_number(stock_number))
    return index.by_collection_number.get(key)


def find_by_fbst(tsv_gz_path: Path, fbst: str) -> CatalogRecord | None:
    """Look up a stock by its FlyBase stock ID (e.g. `FBst0009405`)."""
    index = _get_index(tsv_gz_path)
    return index.by_fbst.get(fbst.strip().lower())


def available_collections(tsv_gz_path: Path) -> dict[str, int]:
    """Collection short-name → count, ordered by descending count.

    Drives the Settings help dialog + the Import dialog's source
    dropdown. Callers don't need to hold the index open.
    """
    index = _get_index(tsv_gz_path)
    return dict(sorted(index.collection_counts.items(), key=lambda kv: (-kv[1], kv[0])))
