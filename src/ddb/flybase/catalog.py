"""File-management side of the FlyBase catalog.

Responsibilities:
  * Resolve on-disk paths under `<data_dir>/flybase/`.
  * Read and write the meta sidecar (`stocks.meta.json`) — what release
    is on disk, when we downloaded it, row count per collection.
  * Discover the current release on FlyBase's S3 mirror by parsing the
    index HTML for `stocks_FB<release>.tsv.gz` filenames.
  * Stream the download with a progress callback so the GUI can draw a
    QProgressBar without the download blocking any signals.
  * Load the postpone sidecar (`stocks.postpone.json`) that the scheduler
    consults.

All network access is via stdlib `urllib.request` — no third-party HTTP
dependency, no background threads here. The GUI wraps each call in a
QThread so the main loop stays responsive.
"""

from __future__ import annotations

import contextlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.request import Request, urlopen

FLYBASE_STOCKS_DIR = (
    "https://s3ftp.flybase.org/releases/current/precomputed_files/stocks/"
)
USER_AGENT = "ddb-drosodb/1.0 (+https://github.com/zerotonin/drosodb)"
_RELEASE_RE = re.compile(r"stocks_(FB\d{4}_\d{2})\.tsv\.gz")


# ----------------------------------------------------------------------
# Paths + meta
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class CatalogPaths:
    """The small set of files we manage under `<data_dir>/flybase/`."""

    root: Path
    tsv_gz: Path  # the download itself
    meta: Path  # sidecar recording release + download time
    postpone: Path  # sidecar recording "snooze until" state

    def exists(self) -> bool:
        return self.tsv_gz.is_file() and self.meta.is_file()


def paths_for(data_dir: Path) -> CatalogPaths:
    root = data_dir / "flybase"
    return CatalogPaths(
        root=root,
        tsv_gz=root / "stocks.tsv.gz",
        meta=root / "stocks.meta.json",
        postpone=root / "stocks.postpone.json",
    )


@dataclass(frozen=True)
class CatalogMeta:
    """Sidecar persisted next to the TSV after a successful download."""

    release: str  # e.g. "FB2026_01"
    downloaded_at: datetime  # tz-aware UTC
    row_count: int
    bytes: int
    collection_counts: dict[str, int]

    def to_json(self) -> str:
        return json.dumps(
            {
                "release": self.release,
                "downloaded_at": self.downloaded_at.astimezone(UTC).isoformat(),
                "row_count": self.row_count,
                "bytes": self.bytes,
                "collection_counts": self.collection_counts,
            },
            indent=2,
        )

    @classmethod
    def from_json(cls, text: str) -> CatalogMeta:
        d = json.loads(text)
        return cls(
            release=str(d["release"]),
            downloaded_at=datetime.fromisoformat(d["downloaded_at"]),
            row_count=int(d["row_count"]),
            bytes=int(d["bytes"]),
            collection_counts={str(k): int(v) for k, v in d["collection_counts"].items()},
        )


def read_meta(paths: CatalogPaths) -> CatalogMeta | None:
    """Return the parsed meta sidecar, or None if it hasn't been written
    or is unreadable. Never raises — a corrupt meta file just means we
    behave as if the catalog was never downloaded."""
    if not paths.meta.is_file():
        return None
    try:
        return CatalogMeta.from_json(paths.meta.read_text())
    except (OSError, ValueError, KeyError):
        return None


def write_meta(paths: CatalogPaths, meta: CatalogMeta) -> None:
    paths.root.mkdir(parents=True, exist_ok=True)
    paths.meta.write_text(meta.to_json())


# ----------------------------------------------------------------------
# Postpone sidecar
# ----------------------------------------------------------------------


def read_postpone(paths: CatalogPaths) -> datetime | None:
    if not paths.postpone.is_file():
        return None
    try:
        d = json.loads(paths.postpone.read_text())
        return datetime.fromisoformat(d["until"])
    except (OSError, ValueError, KeyError):
        return None


def write_postpone(paths: CatalogPaths, until: datetime) -> None:
    paths.root.mkdir(parents=True, exist_ok=True)
    paths.postpone.write_text(
        json.dumps({"until": until.astimezone(UTC).isoformat()})
    )


def clear_postpone(paths: CatalogPaths) -> None:
    with contextlib.suppress(OSError):
        paths.postpone.unlink(missing_ok=True)


# ----------------------------------------------------------------------
# Release discovery + download
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class ReleaseInfo:
    release: str  # e.g. "FB2026_01"
    filename: str  # "stocks_FB2026_01.tsv.gz"
    url: str  # absolute URL


def discover_current_release(*, timeout_s: float = 15.0) -> ReleaseInfo:
    """Parse FlyBase's S3 directory index for the current stocks TSV.

    Raises RuntimeError if the listing is unreachable or contains no
    `stocks_FB*.tsv.gz` filename.
    """
    req = Request(FLYBASE_STOCKS_DIR, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=timeout_s) as resp:
        html = resp.read().decode("utf-8", errors="replace")
    matches = sorted(set(_RELEASE_RE.findall(html)))  # sorted → newest last
    if not matches:
        raise RuntimeError(
            f"no stocks_FB*.tsv.gz found at {FLYBASE_STOCKS_DIR} — "
            "has FlyBase's directory structure changed?"
        )
    release = matches[-1]
    filename = f"stocks_{release}.tsv.gz"
    return ReleaseInfo(release=release, filename=filename, url=FLYBASE_STOCKS_DIR + filename)


@dataclass(frozen=True)
class DownloadProgress:
    """Emitted periodically during a download so the GUI can redraw."""

    bytes_done: int
    bytes_total: int  # 0 when the server didn't send Content-Length


def download_catalog(
    paths: CatalogPaths,
    *,
    release_info: ReleaseInfo | None = None,
    on_progress: Callable[[DownloadProgress], None] | None = None,
    chunk_size: int = 64 * 1024,
    timeout_s: float = 60.0,
) -> CatalogMeta:
    """Download the current (or explicitly-given) release, write the
    meta sidecar, and return the new meta.

    The download streams chunks into a `.tmp` file next to the target
    and renames on success, so a crashed download never leaves a
    truncated TSV where the importer can find it.

    The row count and per-collection totals in the returned meta are
    computed by re-reading the gzipped TSV after download — one extra
    ~2s pass, but the user sees the same summary the Settings tab and
    help dialog will use later.
    """
    info = release_info or discover_current_release(timeout_s=timeout_s)
    paths.root.mkdir(parents=True, exist_ok=True)

    req = Request(info.url, headers={"User-Agent": USER_AGENT})
    tmp = paths.tsv_gz.with_suffix(paths.tsv_gz.suffix + ".tmp")

    bytes_done = 0
    with urlopen(req, timeout=timeout_s) as resp:
        total_hdr = resp.headers.get("Content-Length")
        bytes_total = int(total_hdr) if total_hdr and total_hdr.isdigit() else 0
        with tmp.open("wb") as out:
            while True:
                chunk = resp.read(chunk_size)
                if not chunk:
                    break
                out.write(chunk)
                bytes_done += len(chunk)
                if on_progress is not None:
                    on_progress(DownloadProgress(bytes_done=bytes_done, bytes_total=bytes_total))

    tmp.replace(paths.tsv_gz)

    # Count rows by re-reading the new file. We defer the import to
    # avoid a circular import at module-load time.
    from ddb.flybase.lookup import available_collections, reset_cache

    reset_cache()
    counts = available_collections(paths.tsv_gz)
    row_count = sum(counts.values())

    meta = CatalogMeta(
        release=info.release,
        downloaded_at=datetime.now(UTC),
        row_count=row_count,
        bytes=bytes_done,
        collection_counts=counts,
    )
    write_meta(paths, meta)
    clear_postpone(paths)  # fresh data invalidates any pending snooze
    return meta
