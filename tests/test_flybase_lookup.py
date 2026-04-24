"""Tests for `ddb.flybase.lookup`.

Uses a 5-row fixture TSV written to a temporary gzipped file so every
test exercises the real gzip reader. No network, no BlueZ.
"""

from __future__ import annotations

import gzip
from pathlib import Path

import pytest

from ddb.flybase.lookup import (
    available_collections,
    find_by_fbst,
    find_record,
    reset_cache,
)

_HEADER = (
    "FBst",
    "collection_short_name",
    "stock_type_cv",
    "species",
    "FB_genotype",
    "description",
    "stock_number",
)
_LIVING = "living stock ; FBsv:0000002"
_LOST = "lost stock ; FBsv:0000001"

_FIXTURE_ROWS = [
    _HEADER,
    ("FBst0000002", "Bloomington", _LIVING, "Dmel", "w[*]; betaTub60D[2]", "betaTub", "2"),
    (
        "FBst0009405",
        "Bloomington",
        _LIVING,
        "Dmel",
        "P{ry[+t7.2]=lArB}Adcy1[rut-2080]; P{w[+mC]=UAS-Adcy1.Z}2",
        "rut mutant",
        "9405",
    ),
    (
        "FBst0300004",
        "Kyoto",
        _LIVING,
        "Dmel",
        "0 / FM7a / T(1;Y)B133, y[1] y[+] B[S]",
        "Kyoto 100952",
        "100952",
    ),
    ("FBst0450001", "Vienna", _LIVING, "Dmel", "UAS-RNAi; 2L", "VDRC knockdown", "v10004"),
    ("FBst0099999", "Bloomington", _LOST, "Dmel", "obsolete", "gone", "99999"),
]


@pytest.fixture()
def fixture_tsv(tmp_path: Path) -> Path:
    """Write the fixture rows to a gzipped TSV and return the path."""
    dst = tmp_path / "stocks.tsv.gz"
    with gzip.open(dst, mode="wt", encoding="utf-8", newline="") as fh:
        for row in _FIXTURE_ROWS:
            fh.write("\t".join(row) + "\n")
    reset_cache()  # the in-memory index is module-global
    return dst


def test_finds_by_bdsc_stock_number(fixture_tsv: Path) -> None:
    rec = find_record(fixture_tsv, "Bloomington", "9405")
    assert rec is not None
    assert rec.fbst == "FBst0009405"
    assert rec.stock_number == "9405"
    assert "Adcy1" in rec.genotype


def test_collection_match_is_case_insensitive(fixture_tsv: Path) -> None:
    assert find_record(fixture_tsv, "bloomington", "9405") is not None
    assert find_record(fixture_tsv, "BLOOMINGTON", "9405") is not None


def test_vdrc_accepts_id_with_or_without_v_prefix(fixture_tsv: Path) -> None:
    # Both `v10004` and `10004` should resolve to the same row.
    a = find_record(fixture_tsv, "Vienna", "v10004")
    b = find_record(fixture_tsv, "Vienna", "10004")
    assert a is not None
    assert b is not None
    assert a.fbst == b.fbst == "FBst0450001"


def test_find_by_fbst_direct(fixture_tsv: Path) -> None:
    rec = find_by_fbst(fixture_tsv, "FBst0300004")
    assert rec is not None
    assert rec.collection == "Kyoto"
    assert rec.stock_number == "100952"


def test_fbst_lookup_is_case_insensitive(fixture_tsv: Path) -> None:
    assert find_by_fbst(fixture_tsv, "fbst0300004") is not None


def test_unknown_ids_return_none(fixture_tsv: Path) -> None:
    assert find_record(fixture_tsv, "Bloomington", "99999999") is None
    assert find_by_fbst(fixture_tsv, "FBst9999999") is None
    assert find_record(fixture_tsv, "NotARealCenter", "1") is None


def test_lost_stock_is_still_queryable_but_flagged(fixture_tsv: Path) -> None:
    rec = find_record(fixture_tsv, "Bloomington", "99999")
    assert rec is not None
    assert rec.is_living_stock is False


def test_available_collections_reports_counts(fixture_tsv: Path) -> None:
    counts = available_collections(fixture_tsv)
    assert counts == {"Bloomington": 3, "Kyoto": 1, "Vienna": 1}
    # Ordered by descending count.
    assert list(counts.keys())[0] == "Bloomington"


def test_cache_invalidates_on_file_mtime_change(fixture_tsv: Path, tmp_path: Path) -> None:
    # Prime the cache
    assert find_record(fixture_tsv, "Bloomington", "9405") is not None

    # Rewrite with a new row and bump mtime
    new_rows = list(_FIXTURE_ROWS) + [
        ("FBst0000001", "Bloomington", "living stock", "Dmel", "new", "new", "77777"),
    ]
    with gzip.open(fixture_tsv, mode="wt", encoding="utf-8", newline="") as fh:
        for row in new_rows:
            fh.write("\t".join(row) + "\n")

    # Bump mtime explicitly in case the write happened in the same ns.
    import os
    import time

    future = time.time() + 5
    os.utime(fixture_tsv, (future, future))

    # The new row should now be visible.
    assert find_record(fixture_tsv, "Bloomington", "77777") is not None
