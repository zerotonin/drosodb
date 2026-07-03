#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────
#  DDB — diagnose_user_printer
#  « why is a user's Print button still greyed out? »
# ─────────────────────────────────────────────────────────────────────
#
# For each named user, prints three diagnostics in one pass:
#   1. Their .env on disk (did write_user_printer_env.sh actually land it?)
#   2. What pydantic sees when Python starts cold as that user (rules
#      out .env path or parser bugs)
#   3. Whether a stale GUI process is still running (which would hold
#      the old settings until closed)
#
# Usage:
#   sudo bash scripts/diagnose_user_printer.sh
#     → defaults to: ellavoight jesscarroll
#
#   sudo bash scripts/diagnose_user_printer.sh alice bob
#     → checks those users instead

# Deliberately NOT using `set -e` or `pipefail` — individual section
# failures must not stop us from diagnosing the next user.
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
# │ Clone discovery  « default path, fall back to a search »   │
# └────────────────────────────────────────────────────────────┘
#
# install_user.sh's current default is $HOME/PyProject/drosodb, but
# older installs may have used $HOME/drosodb or something ad-hoc.
# Find whichever is real.

find_repo() {
    local home="$1"
    for candidate in \
        "$home/PyProject/drosodb" \
        "$home/drosodb" \
        "$home/src/drosodb" \
        "$home/code/drosodb"; do
        if [[ -f "$candidate/pyproject.toml" ]]; then
            echo "$candidate"
            return 0
        fi
    done
    # Last resort: search up to 4 levels deep for a pyproject.toml
    # that names "ddb". Silence permission errors.
    find "$home" -maxdepth 4 -name pyproject.toml 2>/dev/null | while read -r pp; do
        if grep -q '^name *= *"ddb"' "$pp" 2>/dev/null; then
            dirname "$pp"
            return 0
        fi
    done
    return 1
}

for u in "${USERS[@]}"; do
    echo "============================================================"
    echo "=== $u"
    echo "============================================================"

    if ! id "$u" &>/dev/null; then
        echo "no such user; skipping"
        continue
    fi

    home=$(getent passwd "$u" | cut -d: -f6)

    echo "--- repo clone location ---"
    repo=$(find_repo "$home")
    if [[ -n "$repo" ]]; then
        echo "  found: $repo"
    else
        echo "  NOT FOUND under $home — has install_user.sh been run for this user?"
        echo "  (checked \$HOME/PyProject/drosodb, \$HOME/drosodb, and a 4-level find)"
        echo
        continue
    fi

    env_file="$repo/.env"
    echo "--- .env on disk ---"
    if [[ -f "$env_file" ]]; then
        ls -la "$env_file"
        cat "$env_file"
    else
        echo "  (missing) $env_file"
        echo "  → run:  sudo bash scripts/write_user_printer_env.sh $u"
        echo "    (note: script assumes \$HOME/PyProject/drosodb — see below)"
    fi

    echo "--- pydantic view (cold python, as $u) ---"
    sudo -u "$u" bash -lc "cd '$repo' && python -c '
from ddb.config import settings
print(f\"  printer_enabled       = {settings.printer_enabled}\")
print(f\"  printer_backend       = {settings.printer_backend}\")
print(f\"  printer_bluetooth_mac = {settings.printer_bluetooth_mac}\")
'" 2>&1 | tail -10

    echo "--- bluetooth group membership ---"
    if id -nG "$u" | tr ' ' '\n' | grep -qx bluetooth; then
        echo "  ✓ in group 'bluetooth'"
    else
        echo "  ✗ NOT in group 'bluetooth'"
        echo "  → run:  sudo usermod -aG bluetooth $u"
        echo "         (takes effect on their next login)"
    fi

    echo "--- is a GUI still running for $u? ---"
    if pgrep -au "$u" -f 'ddb.*gui\|ddb gui' 2>/dev/null; then
        echo "  ↑ a running GUI holds the OLD settings until closed"
    else
        echo "  (no GUI process)"
    fi
    echo
done

echo "=================================================================="
echo "  Legend"
echo "=================================================================="
cat <<'EOF'
If pydantic prints printer_enabled=True but the Print button is still
greyed out for the user, they need to fully close and re-open the GUI
(pydantic reads .env once at process start).

If pydantic prints printer_enabled=False despite the .env on disk
looking correct, the file was written to the wrong path or the DDB
package is being loaded from a different install than the repo shown
above.

If bluetooth-group membership is missing, the GUI's Print button may
still gate open once .env sets printer_enabled=True, but the actual
print will fail at BT socket open with a permission error. Fix both.
EOF
