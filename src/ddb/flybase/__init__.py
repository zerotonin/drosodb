"""Local FlyBase stocks-catalog cache.

FlyBase publishes a consolidated TSV for every ~2-month release that
cross-references stocks from Bloomington, Vienna (VDRC), Kyoto (DGGR),
NIG-Fly, KDRC, FlyORF, and NDSSC. Downloading it once gives us offline
per-ID lookup for every major Drosophila stock center.

This package holds:
  * `catalog`  — file management (download, release discovery, meta).
  * `lookup`   — query the local TSV by (collection, stock_number) or FBst.
  * `parser`   — split a FlyBase genotype string into X/2/3/4/Y components.
  * `scheduler` — "is the catalog stale / is refresh postponed?".

The GUI settings tab + a startup check drive the "when to download"
logic; dialogs call into `lookup` + `parser` for per-import resolution.
"""

from ddb.flybase.catalog import (
    CatalogMeta,
    CatalogPaths,
    DownloadProgress,
    discover_current_release,
    download_catalog,
    paths_for,
    read_meta,
)
from ddb.flybase.lookup import (
    CatalogRecord,
    available_collections,
    find_by_fbst,
    find_record,
)
from ddb.flybase.parser import ParsedGenotype, parse_genotype_string
from ddb.flybase.scheduler import RefreshDecision, decide_refresh

__all__ = [
    "CatalogMeta",
    "CatalogPaths",
    "CatalogRecord",
    "DownloadProgress",
    "ParsedGenotype",
    "RefreshDecision",
    "available_collections",
    "decide_refresh",
    "discover_current_release",
    "download_catalog",
    "find_by_fbst",
    "find_record",
    "parse_genotype_string",
    "paths_for",
    "read_meta",
]
