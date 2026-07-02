#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────
#  DDB — shared-tablet SQLite install
#  « one machine, many humans, one auditable database »
# ─────────────────────────────────────────────────────────────────────
#
# Creates a shared data directory ($SHARED_DIR) owned by a system group
# ($GROUP) with setgid + group-writable permissions, so every tablet
# user in that group can read/write the DDB sqlite file and its label
# PNGs. Every step is idempotent — safe to re-run whenever a new user
# is added or perms drift.
#
# Usage:
#     sudo bash scripts/setup_shared_sqlite.sh
#
# After the script prints its final block, copy
# local_paths.template.json → local_paths.json in the DDB repo and set
# "active_profile" to "shared_tablet".
#
# Uninstall: the script never removes anything. If you decommission the
# tablet, sudo chown -R root:root $SHARED_DIR && sudo chmod -R 700 $SHARED_DIR
# is enough to lock it down.

set -euo pipefail

SHARED_DIR="${DDB_SHARED_DIR:-/srv/ddb}"
GROUP="${DDB_GROUP:-ddb}"

if [[ $EUID -ne 0 ]]; then
    echo "ERROR: must run as root (sudo)." >&2
    exit 1
fi

# ─── 1) Create the group if missing ─────────────────────────────────
if ! getent group "$GROUP" > /dev/null; then
    groupadd --system "$GROUP"
    echo "created group '$GROUP'"
else
    echo "group '$GROUP' already exists — skipping"
fi

# ─── 2) Create the shared directory with setgid on ──────────────────
if [[ ! -d "$SHARED_DIR" ]]; then
    install -d -o root -g "$GROUP" -m 2775 "$SHARED_DIR"
    echo "created $SHARED_DIR (mode 2775, group $GROUP)"
else
    echo "$SHARED_DIR already exists — re-applying group + perms"
fi

# Shared conda-env-pack drop zone. install_user.sh looks here first
# and skips the multi-hundred-MB conda download when a pack is present,
# so tablet #2 and beyond install in seconds. See scripts/pack_env.sh.
if [[ ! -d "$SHARED_DIR/env-packs" ]]; then
    install -d -o root -g "$GROUP" -m 2775 "$SHARED_DIR/env-packs"
    echo "created $SHARED_DIR/env-packs (conda-pack drop zone)"
fi

# ─── 3) Re-assert perms every run (setgid + group-writable) ─────────
chgrp -R "$GROUP" "$SHARED_DIR"
chmod g+rwX,o-rwx "$SHARED_DIR"
# setgid on every subdir so new files inherit the ddb group
find "$SHARED_DIR" -type d -exec chmod 2775 {} +
# and readable/writable by any group member
find "$SHARED_DIR" -type f -exec chmod ug+rw,o-rwx {} +

# ─── 4) Print next-steps for the operator ───────────────────────────
cat <<EOF

────────────────────────────────────────────────────────────────────
 Shared DDB directory ready.
────────────────────────────────────────────────────────────────────
   Path      : $SHARED_DIR
   Group     : $GROUP  (setgid, 2775)
   Perms     : group members can read + write; others locked out

 Next steps:
   1. Add each tablet user to the '$GROUP' group:
          sudo usermod -aG $GROUP <username>
      They must log out and back in for the group to take effect.

   2. In the DDB repo:
          cp local_paths.template.json local_paths.json
          # then edit local_paths.json:
          #   "active_profile": "shared_tablet"

   3. First launch will create the SQLite file with WAL mode enabled
      and auto-register each OS user in the DDB user table (the
      username appears in the status bar bottom-right).

   4. Alembic migrations:
          DDB_DATABASE_URL=sqlite:///$SHARED_DIR/ddb.sqlite3 \\
              alembic upgrade head
      (or edit alembic.ini to point at the shared file).
────────────────────────────────────────────────────────────────────
EOF
