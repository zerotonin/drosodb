#!/usr/bin/env python3
# ╔══════════════════════════════════════════════════════════════════╗
# ║  DDB — ddb_backup                                                ║
# ║  « system-owned hourly snapshot + optional rclone push »         ║
# ╠══════════════════════════════════════════════════════════════════╣
# ║  Runs from the ddb-backup.service oneshot as root, on the        ║
# ║  ddb-backup.timer schedule. Zero conda deps: pure stdlib +       ║
# ║  optional /usr/bin/rclone for offsite.                           ║
# ║                                                                  ║
# ║  Config is loaded from /etc/ddb/backup.env by systemd; every     ║
# ║  knob also has a safe default so a bare install just works.      ║
# ╚══════════════════════════════════════════════════════════════════╝
"""System-level SQLite snapshot + optional rclone push for DDB.

Reads config from environment (populated by systemd's EnvironmentFile).
Uses sqlite3.Connection.backup() so snapshots are consistent under
concurrent writers. Snapshots live under DDB_BACKUP_DEST/history/ with a
stable-name copy at DDB_BACKUP_DEST/ddb.latest.sqlite3.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

# ┌────────────────────────────────────────────────────────────┐
# │ Config  « env vars first, sensible defaults after »        │
# └────────────────────────────────────────────────────────────┘

SRC = Path(os.environ.get("DDB_BACKUP_SRC", "/srv/ddb/ddb.sqlite3"))
DEST = Path(os.environ.get("DDB_BACKUP_DEST", "/srv/ddb/backups"))
RETAIN_HOURLY = int(os.environ.get("DDB_BACKUP_RETAIN_HOURLY", "168"))

RCLONE_CONFIG = Path(os.environ.get("DDB_BACKUP_RCLONE_CONFIG", "/etc/ddb/rclone.conf"))
RCLONE_REMOTE = os.environ.get("DDB_BACKUP_RCLONE_REMOTE", "").strip()
RCLONE_BIN = os.environ.get("DDB_BACKUP_RCLONE_BIN", "/usr/bin/rclone")


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [ddb-backup] %(levelname)s %(message)s",
)
log = logging.getLogger("ddb-backup")


# ┌────────────────────────────────────────────────────────────┐
# │ Snapshot  « SQLite online .backup API »                    │
# └────────────────────────────────────────────────────────────┘


HASH_FILE_NAME = ".last-hash"


def _sha256(path: Path) -> str:
    """Streaming SHA-256 hex digest of *path*."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def snapshot(src: Path, dest_dir: Path) -> tuple[Path | None, str]:
    """Create a consistent snapshot of *src* under *dest_dir*/history/.

    Content-addressed dedupe: after taking the snapshot, its SHA-256 is
    compared against ``dest_dir/.last-hash`` (the hash of the last
    successfully-shipped snapshot). If it matches, the candidate is
    discarded — no history entry, no push, no bump. Only ``ddb.latest``'s
    mtime is touched so log watchers can see we ran and confirmed
    "still current".

    Returns ``(target_path_or_None, sha256_hex)``:
      * ``target_path`` is the retained history file when content changed.
      * ``target_path`` is ``None`` when nothing changed and the caller
        should skip prune + rclone push.
      * ``sha256_hex`` is the hash either way — the caller writes it to
        ``.last-hash`` *only after* any downstream steps (rclone push)
        succeed, so a failed push retries next hour instead of being
        silenced.
    """
    if not src.exists():
        raise FileNotFoundError(f"source DB not found: {src}")

    history = dest_dir / "history"
    history.mkdir(parents=True, exist_ok=True)

    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target = history / f"ddb_{ts}.sqlite3"

    try:
        with sqlite3.connect(str(src)) as source, sqlite3.connect(str(target)) as dst:
            source.backup(dst)
    except Exception:
        # sqlite3.connect(target) already created an empty file; don't
        # leave a 0-byte snapshot behind for prune to keep around.
        target.unlink(missing_ok=True)
        raise

    new_hash = _sha256(target)

    hash_file = dest_dir / HASH_FILE_NAME
    prev_hash = ""
    if hash_file.exists():
        prev_hash = hash_file.read_text(encoding="utf-8").strip()

    latest = dest_dir / "ddb.latest.sqlite3"

    if new_hash == prev_hash:
        # Same content as last shipped snapshot — discard candidate, keep
        # the previous ladder as-is. Touch latest so its mtime reflects
        # "confirmed current at $now".
        target.unlink(missing_ok=True)
        if latest.exists():
            os.utime(latest, None)
        log.info("no change since last snapshot (hash=%s…); skipping", new_hash[:12])
        return None, new_hash

    shutil.copy2(target, latest)
    log.info(
        "snapshot ok: %s (%d bytes, hash=%s…)",
        target.name,
        target.stat().st_size,
        new_hash[:12],
    )
    return target, new_hash


def prune_history(dest_dir: Path, keep: int) -> int:
    """Keep the newest *keep* snapshots in dest_dir/history/. Returns count deleted."""
    history = dest_dir / "history"
    if not history.exists():
        return 0
    snaps = sorted(
        history.glob("ddb_*.sqlite3"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    deleted = 0
    for old in snaps[keep:]:
        old.unlink(missing_ok=True)
        deleted += 1
    if deleted:
        log.info("pruned %d snapshot(s) beyond retention=%d", deleted, keep)
    return deleted


# ┌────────────────────────────────────────────────────────────┐
# │ Offsite push  « rclone, only if configured »               │
# └────────────────────────────────────────────────────────────┘


def rclone_push(dest_dir: Path) -> None:
    """Copy latest + mirror history to the configured rclone remote."""
    if not RCLONE_REMOTE:
        log.info("no rclone remote configured (DDB_BACKUP_RCLONE_REMOTE) — skipping offsite push")
        return

    rclone = Path(RCLONE_BIN)
    if not rclone.exists():
        log.warning(
            "rclone binary not found at %s — skipping offsite push "
            "(install with: sudo apt install rclone)",
            rclone,
        )
        return

    if not RCLONE_CONFIG.exists():
        log.warning(
            "rclone config %s missing — skipping offsite push "
            "(configure with: sudo rclone --config %s config)",
            RCLONE_CONFIG,
            RCLONE_CONFIG,
        )
        return

    remote = RCLONE_REMOTE.rstrip("/")

    latest = dest_dir / "ddb.latest.sqlite3"
    log.info("rclone copy → %s/", remote)
    subprocess.run(
        [
            str(rclone),
            "--config",
            str(RCLONE_CONFIG),
            "copy",
            str(latest),
            f"{remote}/",
            "--log-level",
            "INFO",
        ],
        check=True,
    )

    log.info("rclone sync → %s/history/", remote)
    subprocess.run(
        [
            str(rclone),
            "--config",
            str(RCLONE_CONFIG),
            "sync",
            str(dest_dir / "history"),
            f"{remote}/history/",
            "--log-level",
            "INFO",
        ],
        check=True,
    )


# ┌────────────────────────────────────────────────────────────┐
# │ Main                                                       │
# └────────────────────────────────────────────────────────────┘


def main() -> int:
    log.info(
        "start: src=%s dest=%s retain=%d remote=%s",
        SRC,
        DEST,
        RETAIN_HOURLY,
        RCLONE_REMOTE or "<none>",
    )
    try:
        DEST.mkdir(parents=True, exist_ok=True)
        new_snap, new_hash = snapshot(SRC, DEST)
        if new_snap is None:
            log.info("done (unchanged — no prune, no push)")
            return 0
        prune_history(DEST, RETAIN_HOURLY)
        rclone_push(DEST)
        # Push either succeeded or was intentionally skipped (no remote /
        # rclone missing / config missing — all logged inside rclone_push).
        # Only mark this hash as "shipped" now, so any subprocess failure
        # above bubbles up and the same content gets retried next hour.
        (DEST / HASH_FILE_NAME).write_text(new_hash, encoding="utf-8")
    except Exception as exc:
        log.error("backup failed: %s", exc, exc_info=True)
        return 1
    log.info("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
