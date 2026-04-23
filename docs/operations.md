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

The SQLite DB is snapshotted **hourly** to `~/Crypt/drosodb/` (which is
typically a Syncthing folder, so it ends up on other machines for free).

### How it works

- Script: `~/PyProject/drosodb_backup.sh` (sits **outside** the repo —
  never tracked by git).
- It uses SQLite's online `.backup` API rather than `cp`, so the copy is
  consistent even if the GUI writes during the snapshot.
- Drops two files each hour:
  - `~/Crypt/drosodb/ddb.latest.sqlite3` — stable filename (what
    Syncthing / any live-replica tool should point at).
  - `~/Crypt/drosodb/history/ddb_<UTC-timestamp>.sqlite3` — rollback
    ladder. Keeps the most recent **168** snapshots (one week of
    hourlies); older ones auto-prune.
- Log: `~/PyProject/drosodb_backup.log`.

### Installed cron entry

```
7 * * * * /home/geuba03p/PyProject/drosodb_backup.sh
```

Runs at **:07 past every hour**. Confirm it's live with `crontab -l`.

### Overrides (env vars you can put in the crontab line)

| Variable | Default | Use for |
|---|---|---|
| `DDB_BACKUP_SRC` | `~/PyProject/drosodb/ddb.sqlite3` | Alternative DB path |
| `DDB_BACKUP_DEST` | `~/Crypt/drosodb` | Different backup target |
| `DDB_BACKUP_SQLITE3` | `~/miniconda3/bin/sqlite3` | Alternative sqlite3 binary |
| `DDB_BACKUP_RCLONE` | `~/miniconda3/envs/ddb/bin/rclone` | Alternative rclone binary |

`RETAIN_HOURLY` (default 168) is a constant inside the script — edit
there if you want more or fewer snapshots kept.

### Verifying a snapshot

Any of the `history/` files is a full, valid SQLite database. To poke at
one without disturbing the live DB:

```bash
sqlite3 ~/Crypt/drosodb/history/ddb_20260423T143014Z.sqlite3 \
  "SELECT COUNT(*) FROM vial WHERE is_active = 1;"
```

### Restoring from backup

1. Stop the GUI (`Ctrl-C` in its terminal, or close the window).
2. `cp ~/Crypt/drosodb/ddb.latest.sqlite3 ~/PyProject/drosodb/ddb.sqlite3`
3. Relaunch with `ddb gui`.

Pick a specific `history/` file instead of `ddb.latest.sqlite3` if you
need to roll back further than the last hour.
