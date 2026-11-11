#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────
#  DDB — install_desktop_entry
#  « app-grid entry, desktop icon and launcher for one user »
# ─────────────────────────────────────────────────────────────────────
#
# Run as the user who will launch DDB (no sudo). Safe to re-run; it
# rewrites everything it owns:
#
#   ~/.local/bin/ddb-launcher                 conda-activates, runs `ddb gui`
#   ~/.local/share/icons/hicolor/*/apps/ddb.png   icon, 16-512 px
#   ~/.local/share/applications/ddb.desktop   app grid, search, dock pinning
#   <Desktop>/ddb.desktop                     double-click icon on the desktop
#
# The applications entry matters on GNOME Wayland: DDB declares itself as
# "ddb" (QGuiApplication.setDesktopFileName), and the dock only shows the
# right icon for a running window when ddb.desktop is installed there.
#
# Usage:
#   bash scripts/install_desktop_entry.sh
#
# Environment overrides:
#   DDB_CONDA_ENV    conda env with DDB installed      (default: ddb)
#   DDB_CONDA_SH     path to etc/profile.d/conda.sh    (default: auto-detect)
#   DDB_DESKTOP_DIR  desktop folder                    (default: xdg-user-dir DESKTOP)
#   DDB_NO_DESKTOP_ICON=1   skip the desktop shortcut, keep the app-grid entry

set -euo pipefail

log()  { printf '\033[1;34m▸\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m✗\033[0m %s\n' "$*" >&2; exit 1; }

if [[ $EUID -eq 0 ]]; then
    die "Run this as the user who will launch DDB, not as root."
fi

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONDA_ENV="${DDB_CONDA_ENV:-ddb}"
APPS_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ICON_ROOT="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor"
LAUNCHER="$HOME/.local/bin/ddb-launcher"

# ─── 1) Conda env with DDB ──────────────────────────────────────────

find_conda_sh() {
    if command -v conda > /dev/null 2>&1; then
        local root
        root="$(conda info --base 2>/dev/null || true)"
        [[ -n "$root" && -f "$root/etc/profile.d/conda.sh" ]] \
            && { echo "$root/etc/profile.d/conda.sh"; return 0; }
    fi
    local candidate
    for candidate in \
        "$HOME/miniconda3/etc/profile.d/conda.sh" \
        "$HOME/miniforge3/etc/profile.d/conda.sh" \
        "$HOME/anaconda3/etc/profile.d/conda.sh" \
        "$HOME/mambaforge/etc/profile.d/conda.sh" \
        "/opt/miniconda3/etc/profile.d/conda.sh" \
        "/opt/conda/etc/profile.d/conda.sh"; do
        [[ -f "$candidate" ]] && { echo "$candidate"; return 0; }
    done
    return 1
}

CONDA_SH="${DDB_CONDA_SH:-$(find_conda_sh || true)}"
[[ -n "$CONDA_SH" && -f "$CONDA_SH" ]] \
    || die "conda not found. Run scripts/install_user.sh first, or set DDB_CONDA_SH."

set +u
# shellcheck disable=SC1090
source "$CONDA_SH"
conda activate "$CONDA_ENV" || die "conda env '$CONDA_ENV' not found (set DDB_CONDA_ENV)."
set -u
python -c "import ddb.gui.app" 2>/dev/null \
    || die "DDB's GUI is not installed in env '$CONDA_ENV'. Run scripts/install_user.sh first."
log "conda env: $CONDA_ENV ($CONDA_SH)"

# ─── 2) Launcher shim ───────────────────────────────────────────────
# Exec= in a .desktop file may not contain shell syntax, so the
# conda-activate step lives in this small script instead.

mkdir -p "$(dirname "$LAUNCHER")"
cat > "$LAUNCHER" <<EOF
#!/usr/bin/env bash
# Written by scripts/install_desktop_entry.sh; re-run it to regenerate.
# Activates the DDB conda env and hands off to \`ddb gui\`.
set -eo pipefail
# shellcheck disable=SC1090
source "$CONDA_SH"
conda activate "$CONDA_ENV"
exec ddb gui "\$@"
EOF
chmod +x "$LAUNCHER"
log "launcher: $LAUNCHER"

# ─── 3) Icons ───────────────────────────────────────────────────────

python -m ddb.desktop_icons --icon-root "$ICON_ROOT" --source "$REPO/assets/ddb_logo.png" > /dev/null
if command -v gtk-update-icon-cache > /dev/null 2>&1; then
    gtk-update-icon-cache -q -t -f "$ICON_ROOT" 2>/dev/null || true
fi
log "icons: $ICON_ROOT/{16..512}/apps/ddb.png"

# ─── 4) Desktop entry ───────────────────────────────────────────────

mkdir -p "$APPS_DIR"
ENTRY="$APPS_DIR/ddb.desktop"
cat > "$ENTRY" <<EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=DDB
GenericName=Drosophila vial tracker
Comment=Track Drosophila vials with QR-coded labels
Keywords=fly;Drosophila;vial;stock;QR;label;lab;
Exec=$LAUNCHER
TryExec=$LAUNCHER
Icon=ddb
Categories=Science;Biology;
Terminal=false
StartupNotify=true
StartupWMClass=DDB
EOF

if command -v desktop-file-validate > /dev/null 2>&1; then
    desktop-file-validate "$ENTRY" || warn "desktop-file-validate flagged issues in $ENTRY"
fi
if command -v update-desktop-database > /dev/null 2>&1; then
    update-desktop-database -q "$APPS_DIR" 2>/dev/null || true
fi
log "app grid entry: $ENTRY"

# ─── 5) Desktop shortcut ────────────────────────────────────────────
# GNOME refuses to launch a desktop-folder .desktop file until it is
# executable and marked trusted; set both so the first double-click works.

if [[ "${DDB_NO_DESKTOP_ICON:-0}" != "1" ]]; then
    if [[ -n "${DDB_DESKTOP_DIR:-}" ]]; then
        DESKTOP_DIR="$DDB_DESKTOP_DIR"
    elif command -v xdg-user-dir > /dev/null 2>&1; then
        DESKTOP_DIR="$(xdg-user-dir DESKTOP)"
    else
        DESKTOP_DIR="$HOME/Desktop"
    fi
    mkdir -p "$DESKTOP_DIR"
    SHORTCUT="$DESKTOP_DIR/ddb.desktop"
    install -m 0755 "$ENTRY" "$SHORTCUT"
    if command -v gio > /dev/null 2>&1; then
        gio set "$SHORTCUT" metadata::trusted true 2>/dev/null || true
    fi
    log "desktop icon: $SHORTCUT"
fi

cat <<EOF

────────────────────────────────────────────────────────────────────
 DDB launcher installed for $(id -un).
────────────────────────────────────────────────────────────────────
 • Desktop: double-click the DDB icon.
 • App grid: press Super, type "DDB" (right-click → Pin to Dash).
 • Terminal: ddb-launcher
 If the icon still looks generic, log out and back in once.
────────────────────────────────────────────────────────────────────
EOF
