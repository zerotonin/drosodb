# Day-to-day operations

## Re-printing a label that failed mid-batch

DDB's batch print pauses 1 s between jobs and retries on Bluetooth
`[Errno 16] Device or resource busy`, so most batches go clean. If a
vial still ends up in the DB without a printed label — printer sleeping,
BT drops, out of tape — the dialog tells you how many of the batch
failed. **The vials exist in the DB either way; only the physical label
is missing.**

To re-print one at a time:

1. **Scan tab** → type the print code into the *Code* field (top-right
   of the controls row, next to the camera role dropdown).
2. Press **Enter**. The right-hand Detail panel loads the vial.
3. Click **Print** at the bottom of the Detail panel.

Repeat for each missing label. The print code comes from the dialog's
failure message, or from `data/labels/` on disk where every rendered
label PNG lives under its print code.

If printing from Detail also fails, open a terminal and try:

```bash
ddb printer label <PRINT_CODE>
```

which bypasses the GUI but takes the same BT path; the same retry logic
applies.

## Offline backups

On a shared tablet the DB is written to by whichever biologist happens
to be logged in — nobody's home cron / Syncthing can be assumed to be
running. Backups therefore live at the **system level**: a
`ddb-backup.timer` fires hourly regardless of who is (or isn't) signed
in, and pushes snapshots to a cloud remote via `rclone` if configured.

### One-time install

```bash
sudo bash scripts/setup_system_backup.sh
```

This is idempotent — re-run it after any `git pull` that touches the
scripts. It:

- Creates `/srv/ddb/backups/{,history}` (`root:ddb`, mode 2775 so the
  group inherits automatically).
- Installs the backup script to `/usr/local/sbin/ddb-backup`.
- Installs `ddb-backup.service` (oneshot) + `ddb-backup.timer` (hourly
  with `Persistent=true`) under `/etc/systemd/system/`.
- Seeds `/etc/ddb/backup.env` with commented defaults (won't overwrite
  operator edits on re-runs).
- Enables + starts the timer.

### How it works

- Runs as root from `ddb-backup.service`, so it's independent of any
  user's login session.
- Uses Python's stdlib `sqlite3.Connection.backup()` — a consistent
  copy is guaranteed even while the GUI writes during the snapshot.
- Drops each hour:
  - `/srv/ddb/backups/ddb.latest.sqlite3` — stable filename.
  - `/srv/ddb/backups/history/ddb_<UTC-timestamp>.sqlite3` — rollback
    ladder, most-recent **168** snapshots (one week of hourlies); older
    ones auto-prune.
- Timer runs hourly on `OnCalendar=hourly` with `Persistent=true`, so
  a missed hour (tablet powered off) catches up on next boot.
- Logs to the systemd journal — `journalctl -u ddb-backup.service`.

### Enabling offsite push (optional)

The local snapshots survive a DB corruption but not a stolen tablet.
To ship copies off the machine via `rclone`:

```bash
sudo apt install rclone
sudo rclone --config /etc/ddb/rclone.conf config
# → walk through the wizard to add a remote; call it e.g. "ddb-backup"
sudo $EDITOR /etc/ddb/backup.env
# → set DDB_BACKUP_RCLONE_REMOTE=ddb-backup:drosodb (or your bucket path)
```

The next timer tick will `rclone copy` the latest snapshot and
`rclone sync` the history dir. Cloud remote works with anything rclone
speaks — S3, Google Drive, WebDAV, Nextcloud, ownCloud, Backblaze B2,
etc.

### Config knobs (edit `/etc/ddb/backup.env`)

| Variable | Default | Use for |
|---|---|---|
| `DDB_BACKUP_SRC` | `/srv/ddb/ddb.sqlite3` | Alternative DB path |
| `DDB_BACKUP_DEST` | `/srv/ddb/backups` | Different backup target |
| `DDB_BACKUP_RETAIN_HOURLY` | `168` | Snapshots to retain (one week) |
| `DDB_BACKUP_RCLONE_CONFIG` | `/etc/ddb/rclone.conf` | rclone config path |
| `DDB_BACKUP_RCLONE_REMOTE` | *(empty → skip push)* | e.g. `ddb-backup:drosodb` |
| `DDB_BACKUP_RCLONE_BIN` | `/usr/bin/rclone` | Non-standard rclone binary |

### Verifying a snapshot

Any of the `history/` files is a full, valid SQLite database:

```bash
sqlite3 /srv/ddb/backups/history/ddb_20260703T143014Z.sqlite3 \
  "SELECT COUNT(*) FROM vial WHERE is_active = 1;"
```

Or fire a snapshot manually right now:

```bash
sudo systemctl start ddb-backup.service
journalctl -u ddb-backup.service -n 30
```

### Restoring from backup

1. Stop everyone's GUI (close the app on every logged-in account).
2. `sudo cp /srv/ddb/backups/ddb.latest.sqlite3 /srv/ddb/ddb.sqlite3`
3. Fix ownership + perms:
   `sudo chown geuba03p:ddb /srv/ddb/ddb.sqlite3 && sudo chmod 664 /srv/ddb/ddb.sqlite3`
4. Relaunch with `ddb gui`.

Pick a specific `history/` file instead of `ddb.latest.sqlite3` if you
need to roll back further than the last hour.

### Migrating from the old per-user cron backup

The pre-shared-tablet setup ran a personal per-user backup script
from a crontab, dropping snapshots into a Syncthing folder in the
maintainer's home directory. Once the system timer above has fired at
least once and you've confirmed the new snapshots are landing, retire
the old setup:

```bash
crontab -l                                     # confirm the old line is there
crontab -e                                     # delete the line pointing at the old script
rm -f /path/to/your/old-drosodb_backup.sh      # old script (wherever you kept it)
```
