#!/usr/bin/env python3
# ╔══════════════════════════════════════════════════════════════════╗
# ║  DDB — ddb_to_usb                                                ║
# ║  « vial database onto a USB stick for an offline recording rig » ║
# ╠══════════════════════════════════════════════════════════════════╣
# ║  For rigs without network (no Syncthing). Pure stdlib. Uses the  ║
# ║  SQLite online backup API like ddb_backup.py, so it is safe      ║
# ║  while the DDB GUI is open. The rig imports the stick with       ║
# ║  `rigconductor ddb import` (zerotonin/rigconductor).             ║
# ╚══════════════════════════════════════════════════════════════════╝
"""Copy the DrosoDB database from the bench tablet to a USB stick for the rig.

Run on the tablet (no extra packages needed):

    python3 ddb_to_usb.py                 # picks the only USB stick that is plugged in
    python3 ddb_to_usb.py /media/$USER/STICK

Writes <stick>/drosodb/ddb.latest.sqlite3 and snapshot.json. Uses SQLite's
online backup, so it is safe while the DrosoDB app is open. On the rig:
Setup page → "Import DrosoDB from USB", or `rigconductor ddb import`.
"""

import getpass
import hashlib
import json
import os
import socket
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

SOURCE = Path(os.environ.get("DDB_PATH") or os.environ.get("DDB_BACKUP_SRC") or "/srv/ddb/ddb.sqlite3")


def pick_stick() -> Path:
    base = Path("/media") / getpass.getuser()
    sticks = (
        [p for p in base.iterdir() if p.is_dir() and os.access(p, os.W_OK)] if base.is_dir() else []
    )
    if len(sticks) != 1:
        names = ", ".join(p.name for p in sticks) or "none"
        sys.exit(f"plug in exactly one USB stick (found: {names}) or give its folder as argument")
    return sticks[0]


def main():
    stick = Path(sys.argv[1]) if len(sys.argv) > 1 else pick_stick()
    if not SOURCE.is_file():
        sys.exit(f"DrosoDB not found at {SOURCE} (set DDB_PATH)")
    out_dir = stick / "drosodb"
    out_dir.mkdir(exist_ok=True)
    tmp = out_dir / "ddb.latest.sqlite3.part"
    src = sqlite3.connect(f"file:{SOURCE}?mode=ro", uri=True)
    dst = sqlite3.connect(tmp)
    src.backup(dst)
    n = dst.execute("SELECT count(*) FROM vial WHERE is_active = 1").fetchone()[0]
    ok = dst.execute("PRAGMA integrity_check").fetchone()[0]
    dst.close()
    src.close()
    if ok != "ok":
        tmp.unlink()
        sys.exit(f"integrity check failed: {ok}")
    final = out_dir / "ddb.latest.sqlite3"
    os.replace(tmp, final)
    h = hashlib.sha256(final.read_bytes()).hexdigest()
    meta = {
        "created": datetime.now().isoformat(timespec="seconds"),
        "sha256": h,
        "n_active_vials": n,
        "host": socket.gethostname(),
        "source": str(SOURCE),
    }
    (out_dir / "snapshot.json").write_text(json.dumps(meta, indent=2))
    os.sync()
    print(f"wrote {final} ({n} active vials). Eject the stick, then import it on the rig.")


if __name__ == "__main__":
    main()
