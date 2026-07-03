#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────
#  DDB — setup_my_printer
#  « write my printer .env, verify pydantic sees it — no sudo needed »
# ─────────────────────────────────────────────────────────────────────
#
# Run this AS YOURSELF (not with sudo) on the shared tablet to enable
# the Print button in your DDB GUI. It:
#
#   1. Finds your DDB repo clone under $HOME.
#   2. Writes a .env with DDB_PRINTER_* into it (idempotent — diffs
#      first, only rewrites when content differs).
#   3. Runs a fresh Python that imports ddb.config so you can see
#      what pydantic actually loaded.
#   4. Reminds you if bluetooth-group membership is missing (that
#      part needs the admin to run `sudo usermod -aG bluetooth $USER`
#      once, then log out + back in).
#
# Usage:
#   bash ~/PyProject/drosodb/scripts/setup_my_printer.sh
#   # or, if your clone is elsewhere:
#   bash /path/to/drosodb/scripts/setup_my_printer.sh
#
# After it prints "printer_enabled = True", close and re-open the DDB
# GUI. Pydantic reads .env once at process start — a running GUI keeps
# its old settings until you restart it.

set -u

if [[ $EUID -eq 0 ]]; then
    echo "Run this as YOURSELF, not with sudo. It only touches your own home." >&2
    exit 1
fi

# ┌────────────────────────────────────────────────────────────┐
# │ Printer settings  « keep in sync with the tablet's admin » │
# └────────────────────────────────────────────────────────────┘

ENV_CONTENT=$(cat <<'EOF'
DDB_PRINTER_ENABLED=1
DDB_PRINTER_BACKEND=bluetooth
DDB_PRINTER_MODEL=QL-820NWB
DDB_PRINTER_LABEL=17x54
DDB_PRINTER_BLUETOOTH_MAC=AC:4D:16:EB:B6:44
EOF
)

# ┌────────────────────────────────────────────────────────────┐
# │ Clone discovery                                            │
# └────────────────────────────────────────────────────────────┘

find_my_repo() {
    for candidate in \
        "$HOME/PyProject/drosodb" \
        "$HOME/drosodb" \
        "$HOME/src/drosodb" \
        "$HOME/code/drosodb"; do
        if [[ -f "$candidate/pyproject.toml" ]]; then
            echo "$candidate"
            return 0
        fi
    done
    find "$HOME" -maxdepth 4 -name pyproject.toml 2>/dev/null | while read -r pp; do
        if grep -q '^name *= *"ddb"' "$pp" 2>/dev/null; then
            dirname "$pp"
            return 0
        fi
    done
    return 1
}

repo=$(find_my_repo)
if [[ -z "$repo" ]]; then
    echo "Could not find a DDB clone under $HOME." >&2
    echo "Expected one of: ~/PyProject/drosodb, ~/drosodb, ~/src/drosodb, ~/code/drosodb" >&2
    echo "If it lives elsewhere, ask the tablet admin to run install_user.sh for you." >&2
    exit 1
fi
echo "Found repo: $repo"

# ┌────────────────────────────────────────────────────────────┐
# │ Write .env (idempotent)                                    │
# └────────────────────────────────────────────────────────────┘

env_file="$repo/.env"
if [[ -f "$env_file" ]] \
    && diff -q <(printf '%s\n' "$ENV_CONTENT") "$env_file" >/dev/null 2>&1; then
    echo "ok:    $env_file already up to date"
else
    printf '%s\n' "$ENV_CONTENT" > "$env_file"
    chmod 644 "$env_file"
    echo "wrote: $env_file"
fi

# ┌────────────────────────────────────────────────────────────┐
# │ Verify with a fresh Python                                 │
# └────────────────────────────────────────────────────────────┘

echo
echo "--- pydantic view (fresh Python) ---"
(cd "$repo" && python -c '
from ddb.config import settings
print(f"  printer_enabled       = {settings.printer_enabled}")
print(f"  printer_backend       = {settings.printer_backend}")
print(f"  printer_bluetooth_mac = {settings.printer_bluetooth_mac}")
') 2>&1 | tail -10

# ┌────────────────────────────────────────────────────────────┐
# │ Bluetooth group check                                      │
# └────────────────────────────────────────────────────────────┘

echo
echo "--- bluetooth group membership ---"
if id -nG | tr ' ' '\n' | grep -qx bluetooth; then
    echo "  in group 'bluetooth' — BT socket access is allowed"
else
    echo "  NOT in group 'bluetooth'"
    echo "  → ask the tablet admin to run once:"
    echo "        sudo usermod -aG bluetooth $USER"
    echo "    then log OUT and back in for the new group to take effect"
fi

# ┌────────────────────────────────────────────────────────────┐
# │ Next-step reminder                                         │
# └────────────────────────────────────────────────────────────┘

echo
echo "=================================================================="
echo "  Now fully close and re-open the DDB GUI. Pydantic reads .env"
echo "  once at process start, so a running GUI keeps the old settings"
echo "  until you restart it."
echo "=================================================================="
