#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────
#  DDB — update_user_installs
#  « update other users' clones and (re)install their launchers »
# ─────────────────────────────────────────────────────────────────────
#
# For each named user, acting AS that user (so file ownership stays
# theirs):
#
#   1. find their DDB clone (same discovery as the other admin scripts);
#   2. fast-forward it from its upstream: refuses on local edits to
#      tracked files or local commits, like Settings → Updates does;
#   3. reinstall into their conda env only if the new version needs
#      packages that env lacks (ddb.updater decides);
#   4. run scripts/install_desktop_entry.sh: launcher, icons, app-grid
#      entry, desktop icon.
#
# One user's failure doesn't stop the others. Clones update from GitHub,
# so push your commits first.
#
# Usage:
#   sudo bash scripts/update_user_installs.sh             → ellavoight jesscarroll
#   sudo bash scripts/update_user_installs.sh alice bob
#   bash scripts/update_user_installs.sh "$USER"          → yourself, no sudo
#
# Environment:
#   DDB_CONDA_ENV   conda env name in every user's install (default: ddb)

set -u

CONDA_ENV="${DDB_CONDA_ENV:-ddb}"

if [[ $# -gt 0 ]]; then
    USERS=("$@")
else
    USERS=(ellavoight jesscarroll)
fi

ME="$(id -un)"
if [[ $EUID -ne 0 ]]; then
    for u in "${USERS[@]}"; do
        if [[ "$u" != "$ME" ]]; then
            echo "Updating other users needs root. Try: sudo bash $0 ${USERS[*]}" >&2
            exit 1
        fi
    done
fi

cd / || exit 1  # the caller's cwd may be unreadable to the target users

# ┌────────────────────────────────────────────────────────────┐
# │ Helpers  « run as the target user, find their install »    │
# └────────────────────────────────────────────────────────────┘

as_user() {
    local user="$1"
    shift
    if [[ "$user" == "$ME" ]]; then
        "$@"
    else
        sudo -u "$user" -H "$@"
    fi
}

find_repo() {
    local home="$1" candidate
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
    find "$home" -maxdepth 4 -name pyproject.toml 2>/dev/null | while read -r pp; do
        if grep -q '^name *= *"ddb"' "$pp" 2>/dev/null; then
            dirname "$pp"
            return 0
        fi
    done | head -1
}

find_conda_sh() {
    local home="$1" candidate
    for candidate in \
        "$home/miniforge3/etc/profile.d/conda.sh" \
        "$home/miniconda3/etc/profile.d/conda.sh" \
        "$home/anaconda3/etc/profile.d/conda.sh" \
        "$home/mambaforge/etc/profile.d/conda.sh" \
        "/opt/miniconda3/etc/profile.d/conda.sh" \
        "/opt/conda/etc/profile.d/conda.sh"; do
        if [[ -f "$candidate" ]]; then
            echo "$candidate"
            return 0
        fi
    done
    return 1
}

# ┌────────────────────────────────────────────────────────────┐
# │ Steps  « update, reinstall if needed, desktop entry »      │
# └────────────────────────────────────────────────────────────┘

fast_forward() {
    local u="$1" repo="$2" dirty counts ahead behind
    if ! as_user "$u" env GIT_TERMINAL_PROMPT=0 \
            GIT_SSH_COMMAND="ssh -o BatchMode=yes -o ConnectTimeout=10" \
            timeout 60 git -C "$repo" fetch --quiet; then
        echo "  ✗ git fetch failed (offline, or no access to the remote)"
        return 1
    fi
    dirty="$(as_user "$u" git -C "$repo" status --porcelain --untracked-files=no)"
    if [[ -n "$dirty" ]]; then
        echo "  ✗ local edits to tracked files; not updating:"
        echo "$dirty" | sed 's/^/      /'
        return 1
    fi
    counts="$(as_user "$u" git -C "$repo" rev-list --left-right --count 'HEAD...@{u}')" || {
        echo "  ✗ the clone's branch has no upstream"
        return 1
    }
    read -r ahead behind <<< "$counts"
    if [[ "$behind" -eq 0 ]]; then
        echo "  ✓ already up to date ($(as_user "$u" git -C "$repo" log -1 --format='%h %cs'))"
        return 0
    fi
    if [[ "$ahead" -gt 0 ]]; then
        echo "  ✗ $ahead local commit(s) not on the upstream; update this clone by hand"
        return 1
    fi
    if as_user "$u" git -C "$repo" merge --ff-only --quiet '@{u}'; then
        echo "  ✓ updated $behind commit(s) → $(as_user "$u" git -C "$repo" log -1 --format='%h %cs')"
        return 0
    fi
    echo "  ✗ fast-forward failed"
    return 1
}

reinstall_if_needed() {
    local u="$1" repo="$2" env_py="$3" missing
    if ! as_user "$u" "$env_py" -c "import ddb.updater" 2>/dev/null; then
        echo "  ✗ DDB isn't importable from $env_py"
        return 1
    fi
    missing="$(as_user "$u" "$env_py" - "$repo" <<'PY'
import sys
from pathlib import Path

from ddb.updater import console_scripts_changed, find_unmet_requirements

text = Path(sys.argv[1], "pyproject.toml").read_text(encoding="utf-8")
unmet = list(find_unmet_requirements(text))
if console_scripts_changed(text):
    unmet.append("(console scripts)")
print(" ".join(unmet))
PY
)"
    if [[ -z "$missing" ]]; then
        echo "  ✓ environment already has every required package"
        return 0
    fi
    echo "  ▸ installing: $missing"
    if as_user "$u" "$env_py" -m pip install --quiet -e "$repo[gui,hardware]"; then
        echo "  ✓ reinstalled"
        return 0
    fi
    echo "  ✗ pip install failed; run as $u: pip install -e \"$repo[gui,hardware]\""
    return 1
}

desktop_entry() {
    local u="$1" repo="$2" conda_sh="$3" uid bus
    local script="$repo/scripts/install_desktop_entry.sh"
    if [[ ! -f "$script" ]]; then
        echo "  ✗ $script missing; push the commit that adds it, then re-run"
        return 1
    fi
    uid="$(id -u "$u")"
    bus="/run/user/$uid/bus"
    local -a session_env=()
    if [[ -S "$bus" ]]; then
        # Logged in: reach their session so `gio` can mark the icon trusted.
        session_env=(DBUS_SESSION_BUS_ADDRESS="unix:path=$bus" XDG_RUNTIME_DIR="/run/user/$uid")
    fi
    if as_user "$u" env "${session_env[@]}" DDB_CONDA_SH="$conda_sh" DDB_CONDA_ENV="$CONDA_ENV" \
            bash "$script" > /tmp/ddb-desktop-entry-"$u".log 2>&1; then
        echo "  ✓ launcher, icons, app-grid entry and desktop icon installed"
        if [[ ${#session_env[@]} -eq 0 ]]; then
            echo "    ($u isn't logged in, so the desktop icon may ask once:"
            echo "     right-click it → Allow Launching. The app grid entry needs nothing.)"
        fi
        rm -f /tmp/ddb-desktop-entry-"$u".log
        return 0
    fi
    echo "  ✗ install_desktop_entry.sh failed; log: /tmp/ddb-desktop-entry-$u.log"
    return 1
}

# ┌────────────────────────────────────────────────────────────┐
# │ Main                                                       │
# └────────────────────────────────────────────────────────────┘

FAILED=()
for u in "${USERS[@]}"; do
    echo "══ $u"
    if ! id "$u" > /dev/null 2>&1; then
        echo "  ✗ no such user"
        FAILED+=("$u")
        continue
    fi
    home="$(getent passwd "$u" | cut -d: -f6)"
    repo="$(find_repo "$home")"
    if [[ -z "$repo" ]]; then
        echo "  ✗ no DDB clone found under $home"
        FAILED+=("$u")
        continue
    fi
    echo "  clone: $repo"
    conda_sh="$(find_conda_sh "$home")"
    if [[ -z "$conda_sh" ]]; then
        echo "  ✗ no conda install found for $u"
        FAILED+=("$u")
        continue
    fi
    env_py="$(dirname "$(dirname "$(dirname "$conda_sh")")")/envs/$CONDA_ENV/bin/python"
    if pgrep -u "$u" -f "ddb.*gui" > /dev/null 2>&1; then
        echo "  ! DDB is running for $u; they need to restart it after this"
    fi

    ok=true
    fast_forward "$u" "$repo" || ok=false
    reinstall_if_needed "$u" "$repo" "$env_py" || ok=false
    desktop_entry "$u" "$repo" "$conda_sh" || ok=false
    $ok || FAILED+=("$u")
done

echo
echo "=================================================================="
if [[ ${#FAILED[@]} -eq 0 ]]; then
    echo "  All done: ${USERS[*]}"
else
    echo "  Needs attention: ${FAILED[*]}"
fi
echo "=================================================================="
[[ ${#FAILED[@]} -eq 0 ]]
