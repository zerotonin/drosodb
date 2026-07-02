#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────
#  DDB — setup_system_backup
#  « installs the system-owned hourly backup timer »
# ─────────────────────────────────────────────────────────────────────
#
# Idempotent — re-run to update the script + units after a git pull.
#
#   sudo bash scripts/setup_system_backup.sh
#
# What it does:
#   1. Ensures /srv/ddb/backups/{,history} exist, root:ddb mode 2775
#   2. Ensures /etc/ddb/ exists for backup.env + rclone.conf
#   3. Copies scripts/ddb_backup.py    → /usr/local/sbin/ddb-backup
#   4. Copies scripts/ddb-backup.service → /etc/systemd/system/
#   5. Copies scripts/ddb-backup.timer   → /etc/systemd/system/
#   6. Seeds /etc/ddb/backup.env with commented defaults (won't clobber
#      an existing file — operator edits survive re-runs)
#   7. daemon-reload + enable --now the timer
#
# After installing:
#   - Test:              sudo systemctl start ddb-backup.service
#   - Watch logs:        journalctl -u ddb-backup.service -f
#   - Confirm timer:     systemctl list-timers ddb-backup.timer
#   - Offsite push:      see "Enabling offsite push" note printed at end.

set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "This script must be run as root. Try: sudo bash $0" >&2
    exit 1
fi

# Locate repo scripts dir relative to this file, resolving symlinks.
SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"

if [[ ! -f "$SCRIPT_DIR/ddb_backup.py" ]]; then
    echo "Cannot find ddb_backup.py next to this installer. Aborting." >&2
    exit 1
fi

echo "→ Ensuring /srv/ddb/backups + /srv/ddb/backups/history exist"
install -d -m 2775 -o root -g ddb /srv/ddb/backups
install -d -m 2775 -o root -g ddb /srv/ddb/backups/history

echo "→ Ensuring /etc/ddb/ exists"
install -d -m 0755 /etc/ddb

echo "→ Installing /usr/local/sbin/ddb-backup"
install -m 0755 "$SCRIPT_DIR/ddb_backup.py" /usr/local/sbin/ddb-backup

echo "→ Installing systemd units"
install -m 0644 "$SCRIPT_DIR/ddb-backup.service" /etc/systemd/system/ddb-backup.service
install -m 0644 "$SCRIPT_DIR/ddb-backup.timer" /etc/systemd/system/ddb-backup.timer

if [[ ! -f /etc/ddb/backup.env ]]; then
    echo "→ Seeding /etc/ddb/backup.env with commented defaults"
    cat > /etc/ddb/backup.env <<'EOF'
# DDB backup config — read by ddb-backup.service via EnvironmentFile.
# Uncomment + edit the values you want to change. Every setting has
# a safe built-in default, so leaving this file empty is fine for
# the local-only case.

# Path to the live SQLite DB (source of snapshots)
# DDB_BACKUP_SRC=/srv/ddb/ddb.sqlite3

# Where snapshots are written (must be on a filesystem the ddb-backup
# service can write to; see ReadWritePaths= in the unit if you change this)
# DDB_BACKUP_DEST=/srv/ddb/backups

# Number of hourly snapshots to retain (one week default)
# DDB_BACKUP_RETAIN_HOURLY=168

# Offsite push via rclone — leave DDB_BACKUP_RCLONE_REMOTE empty to
# skip the push entirely.
#
# One-time setup (run as root so the config lands in /etc/ddb/):
#   sudo apt install rclone
#   sudo rclone --config /etc/ddb/rclone.conf config
#
# Then uncomment and set the two variables below, matching whatever
# you called the remote in `rclone config`.
#
# DDB_BACKUP_RCLONE_CONFIG=/etc/ddb/rclone.conf
# DDB_BACKUP_RCLONE_REMOTE=ddb-backup:drosodb
EOF
    chmod 0644 /etc/ddb/backup.env
else
    echo "→ /etc/ddb/backup.env already exists — leaving operator edits intact"
fi

echo "→ Reloading systemd"
systemctl daemon-reload

echo "→ Enabling + starting ddb-backup.timer"
systemctl enable --now ddb-backup.timer

echo
echo "=================================================================="
echo "  Timer installed. Status:"
echo "=================================================================="
systemctl list-timers ddb-backup.timer --no-pager || true
echo

cat <<'EOF'
Next steps:

  1. Fire one snapshot right now to confirm the pipeline works:
       sudo systemctl start ddb-backup.service
       ls -lt /srv/ddb/backups/history/ | head

  2. Watch live logs:
       journalctl -u ddb-backup.service -f

  3. Enable offsite push (optional — the timer works without it):
       sudo apt install rclone
       sudo rclone --config /etc/ddb/rclone.conf config
     Then edit /etc/ddb/backup.env:
       DDB_BACKUP_RCLONE_CONFIG=/etc/ddb/rclone.conf
       DDB_BACKUP_RCLONE_REMOTE=<name-from-rclone-config>:drosodb

  4. Remove any old per-user cron entry (once the new timer has fired
     at least once and you're satisfied it's working):
       crontab -l                                   # confirm it's there
       crontab -e                                   # delete the line
       # delete the old script wherever you kept it
EOF
