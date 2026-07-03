#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────
#  DDB — write_user_printer_env
#  « drop DDB_PRINTER_* config into each user's repo clone »
# ─────────────────────────────────────────────────────────────────────
#
# Every user on the shared tablet has their own git clone of DDB under
# $HOME/PyProject/drosodb (see install_user.sh). The Print button in
# the GUI is greyed out until DDB_PRINTER_ENABLED=1 is set — which
# lives in the per-user .env inside that clone.
#
# This script writes a matching .env into each named user's clone.
# Idempotent: it diffs against the existing .env and only rewrites when
# content differs.
#
# Usage:
#   sudo bash scripts/write_user_printer_env.sh
#     → defaults to: ellavoight jesscarroll
#
#   sudo bash scripts/write_user_printer_env.sh alice bob
#     → writes to those users' clones instead
#
# Note: the printer group membership (`sudo usermod -aG bluetooth <user>`)
# is a SEPARATE prerequisite — without it BlueZ refuses the socket even
# with the .env in place. Run that once per user; they log out + back in.

set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "This script must be run as root. Try: sudo bash $0" >&2
    exit 1
fi

if [[ $# -gt 0 ]]; then
    USERS=("$@")
else
    USERS=(ellavoight jesscarroll)
fi

# ┌────────────────────────────────────────────────────────────┐
# │ Printer settings  « keep in sync with your own .env »      │
# └────────────────────────────────────────────────────────────┘
#
# The MAC + model here match the tablet's Brother QL-820NWB. If you
# swap printers, edit this block and re-run the script.

ENV_CONTENT=$(cat <<'EOF'
DDB_PRINTER_ENABLED=1
DDB_PRINTER_BACKEND=bluetooth
DDB_PRINTER_MODEL=QL-820NWB
DDB_PRINTER_LABEL=17x54
DDB_PRINTER_BLUETOOTH_MAC=AC:4D:16:EB:B6:44
EOF
)

# ┌────────────────────────────────────────────────────────────┐
# │ Main                                                       │
# └────────────────────────────────────────────────────────────┘

STATUS_OK=0
STATUS_SKIPPED=0
STATUS_UPDATED=0

for u in "${USERS[@]}"; do
    if ! id "$u" &>/dev/null; then
        echo "skip: $u — no such user"
        STATUS_SKIPPED=$((STATUS_SKIPPED + 1))
        continue
    fi

    home=$(getent passwd "$u" | cut -d: -f6)
    repo="$home/PyProject/drosodb"
    env_file="$repo/.env"

    if [[ ! -d "$repo" ]]; then
        echo "skip: $u — no repo clone at $repo (has install_user.sh been run?)"
        STATUS_SKIPPED=$((STATUS_SKIPPED + 1))
        continue
    fi

    if [[ -f "$env_file" ]] \
        && diff -q <(printf '%s\n' "$ENV_CONTENT") "$env_file" >/dev/null 2>&1; then
        echo "ok:   $u — $env_file already up to date"
        STATUS_OK=$((STATUS_OK + 1))
        continue
    fi

    printf '%s\n' "$ENV_CONTENT" > "$env_file"
    chown "$u:$u" "$env_file"
    chmod 644 "$env_file"
    echo "wrote: $u — $env_file"
    STATUS_UPDATED=$((STATUS_UPDATED + 1))
done

echo
echo "=================================================================="
echo "  Summary: $STATUS_UPDATED updated, $STATUS_OK already ok, $STATUS_SKIPPED skipped"
echo "=================================================================="
echo
echo "Next step for each user whose .env was written or updated:"
echo "  → Close and re-open the DDB GUI."
echo "    Pydantic reads .env once at process startup, so a running"
echo "    session won't pick up the new settings."
echo
echo "Reminder: printer access also needs bluetooth-group membership,"
echo "which is applied at next login:"
echo "  sudo usermod -aG bluetooth <user>"
