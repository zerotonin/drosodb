#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────
#  DDB — write_user_camera_config
#  « copy the tablet's camera role→bus_path map into each user's home »
# ─────────────────────────────────────────────────────────────────────
#
# Camera role assignments live in ~/.config/ddb/cameras.json (per-user).
# On a shared tablet every user's cameras.json can be identical, because
# USB bus paths ("1-6.1") are physical hardware identifiers tied to the
# physical port, not to the user or the current /dev/videoN number.
#
# This script copies the current admin's cameras.json into each named
# user's ~/.config/ddb/ so they don't have to run `ddb camera assign`
# interactively. Idempotent — diffs before overwriting.
#
# Usage:
#   sudo bash scripts/write_user_camera_config.sh
#     → source: $SUDO_USER's cameras.json (or override via DDB_SOURCE_JSON)
#     → targets: ellavoight jesscarroll
#
#   sudo bash scripts/write_user_camera_config.sh alice bob
#     → same source, different targets
#
#   sudo DDB_SOURCE_JSON=/path/to/cameras.json bash scripts/write_user_camera_config.sh
#     → explicit source file

set -u

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
# │ Source cameras.json                                        │
# └────────────────────────────────────────────────────────────┘

if [[ -n "${DDB_SOURCE_JSON:-}" ]]; then
    SRC="$DDB_SOURCE_JSON"
elif [[ -n "${SUDO_USER:-}" ]]; then
    src_home=$(getent passwd "$SUDO_USER" | cut -d: -f6)
    SRC="$src_home/.config/ddb/cameras.json"
else
    SRC="/root/.config/ddb/cameras.json"
fi

if [[ ! -f "$SRC" ]]; then
    echo "Source cameras.json not found: $SRC" >&2
    echo "The admin's account needs to have run 'ddb camera assign' at least once," >&2
    echo "or pass DDB_SOURCE_JSON=/absolute/path/to/cameras.json." >&2
    exit 1
fi

echo "Source: $SRC"
echo "        $(cat "$SRC" | tr -d '\n' | cut -c-80)"
echo

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
    dst_dir="$home/.config/ddb"
    dst="$dst_dir/cameras.json"

    if [[ -f "$dst" ]] && diff -q "$SRC" "$dst" >/dev/null 2>&1; then
        echo "ok:    $u — $dst already up to date"
        STATUS_OK=$((STATUS_OK + 1))
        continue
    fi

    install -d -m 0755 -o "$u" -g "$u" "$dst_dir"
    install -m 0644 -o "$u" -g "$u" "$SRC" "$dst"
    echo "wrote: $u — $dst"
    STATUS_UPDATED=$((STATUS_UPDATED + 1))
done

echo
echo "=================================================================="
echo "  Summary: $STATUS_UPDATED updated, $STATUS_OK already ok, $STATUS_SKIPPED skipped"
echo "=================================================================="
echo
echo "The Scan tab re-reads this file every time you click Start,"
echo "so affected users don't need to restart the GUI — just click Start."
