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


def snapshot(src: Path, dest_dir: Path) -> Path:
    """Create a consistent snapshot of *src* under *dest_dir*/history/."""
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

    latest = dest_dir / "ddb.latest.sqlite3"
    shutil.copy2(target, latest)
    log.info("snapshot ok: %s (%d bytes)", target.name, target.stat().st_size)
    return target


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
        snapshot(SRC, DEST)
        prune_history(DEST, RETAIN_HOURLY)
        rclone_push(DEST)
    except Exception as exc:
        log.error("backup failed: %s", exc, exc_info=True)
        return 1
    log.info("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
