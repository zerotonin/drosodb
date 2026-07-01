#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────
#  DDB — per-user install (no sudo required)
#  « clone → conda env → editable install → desktop launcher »
# ─────────────────────────────────────────────────────────────────────
#
# What this does — for the calling Linux account only:
#   1. Sanity-check that conda is available (or point at how to install it).
#   2. Clone the repo to $INSTALL_DIR (or fast-forward it if already there).
#   3. Create / update the `ddb` conda env from environment.yml.
#   4. Editable-install DDB into that env with the gui + hardware extras.
#   5. Copy local_paths.template.json → local_paths.json if it's missing
#      (safe default: `local` profile — user edits it to switch to the
#      shared-tablet profile when applicable).
#   6. Drop a .desktop launcher on the user's Desktop that launches
#      `ddb gui` inside the activated env.
#
# Nothing here needs sudo. The shared-tablet setup script
# (scripts/setup_shared_sqlite.sh) is a separate, sudo-only step run
# ONCE by whoever admins the tablet.
#
# Two ways to run:
#
#   # (a) One-liner — bootstraps everything, no pre-clone needed:
#   curl -sSL \
#     https://raw.githubusercontent.com/zerotonin/drosodb/main/scripts/install_user.sh \
#     | bash
#
#   # (b) You already cloned the repo somewhere and just want to (re-)install:
#   bash scripts/install_user.sh
#
# Env-var overrides (rare):
#   DDB_INSTALL_DIR   default: $HOME/PyProject/drosodb
#   DDB_CONDA_ENV     default: ddb
#   DDB_REPO_URL      default: https://github.com/zerotonin/drosodb.git
#   DDB_BRANCH        default: main
#   DDB_DESKTOP_DIR   default: $(xdg-user-dir DESKTOP) or $HOME/Desktop

set -euo pipefail

INSTALL_DIR="${DDB_INSTALL_DIR:-$HOME/PyProject/drosodb}"
CONDA_ENV="${DDB_CONDA_ENV:-ddb}"
REPO_URL="${DDB_REPO_URL:-https://github.com/zerotonin/drosodb.git}"
BRANCH="${DDB_BRANCH:-main}"

log()  { printf '\033[1;34m▸\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m✗\033[0m %s\n' "$*" >&2; exit 1; }

# ─── 1) Locate conda ────────────────────────────────────────────────
find_conda_sh() {
    # Prefer an initialized conda, then walk the usual per-user + system
    # install locations. Print nothing on failure — the caller decides.
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

CONDA_SH="$(find_conda_sh || true)"
if [[ -z "$CONDA_SH" ]]; then
    die "conda not found. Install Miniforge into your home directory first — no sudo needed:
    curl -L -o /tmp/mf.sh https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Linux-x86_64.sh
    bash /tmp/mf.sh -b -p \"\$HOME/miniforge3\"
    \"\$HOME/miniforge3/bin/conda\" init bash
    exec bash          # then re-run this installer"
fi
log "using conda at: $CONDA_SH"

# ─── 2) Clone or fast-forward the repo ──────────────────────────────
# If we're already inside a checkout (either the pre-cloned repo the
# user is running the script from, or a script piped from curl), auto-
# detect and reuse — no accidental second copy.
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || true)"
if [[ -n "$script_dir" && -d "$script_dir/../.git" ]]; then
    detected="$(git -C "$script_dir/.." rev-parse --show-toplevel 2>/dev/null || true)"
    if [[ -n "$detected" ]]; then
        INSTALL_DIR="$detected"
        log "using existing checkout: $INSTALL_DIR"
    fi
fi

if [[ -d "$INSTALL_DIR/.git" ]]; then
    log "repo already cloned — pulling latest on '$BRANCH'"
    git -C "$INSTALL_DIR" fetch origin "$BRANCH"
    git -C "$INSTALL_DIR" checkout "$BRANCH"
    git -C "$INSTALL_DIR" pull --ff-only origin "$BRANCH" \
        || warn "fast-forward failed — resolve manually and re-run"
else
    log "cloning $REPO_URL → $INSTALL_DIR"
    mkdir -p "$(dirname "$INSTALL_DIR")"
    git clone --branch "$BRANCH" "$REPO_URL" "$INSTALL_DIR"
fi

# ─── 3) Create or update the conda env ──────────────────────────────
# shellcheck disable=SC1090
source "$CONDA_SH"

if conda env list | awk '{print $1}' | grep -qxF "$CONDA_ENV"; then
    log "conda env '$CONDA_ENV' exists — updating (prune orphans)"
    conda env update -n "$CONDA_ENV" -f "$INSTALL_DIR/environment.yml" --prune
else
    log "creating conda env '$CONDA_ENV' from environment.yml"
    conda env create -n "$CONDA_ENV" -f "$INSTALL_DIR/environment.yml"
fi

conda activate "$CONDA_ENV"

# ─── 4) Editable install + extras ───────────────────────────────────
log "editable install of DDB with [gui,hardware,dev] extras"
(
    cd "$INSTALL_DIR"
    pip install -e ".[gui,hardware,dev]"
)

# ─── 5) local_paths.json — copy from template if missing ────────────
if [[ ! -f "$INSTALL_DIR/local_paths.json" ]]; then
    cp "$INSTALL_DIR/local_paths.template.json" "$INSTALL_DIR/local_paths.json"
    warn "local_paths.json created from template."
    warn "If this tablet is set up for shared use, edit it and set:"
    warn "    \"active_profile\": \"shared_tablet\""
fi

# ─── 6) Per-user launcher script + desktop file ─────────────────────
# The .desktop spec is strict about reserved chars (& " ' ...) in the
# Exec value, so embedding `bash -c 'source ... && conda activate ...'`
# inline is not spec-compliant and desktop-file-validate rightly rejects
# it. Instead we drop a small shim in ~/.local/bin that does the
# conda-activate dance, and Exec= points at that. Clean, spec-compliant,
# and it doubles as a terminal shortcut ("ddb-launcher") on the PATH.
LOCAL_BIN="$HOME/.local/bin"
mkdir -p "$LOCAL_BIN"
LAUNCHER="$LOCAL_BIN/ddb-launcher"
cat > "$LAUNCHER" <<EOF
#!/usr/bin/env bash
# Auto-generated by scripts/install_user.sh — re-run the installer to
# regenerate. Activates the DDB conda env and hands off to \`ddb gui\`.
set -euo pipefail
# shellcheck disable=SC1090
source "$CONDA_SH"
conda activate "$CONDA_ENV"
exec ddb gui "\$@"
EOF
chmod +x "$LAUNCHER"
log "launcher shim: $LAUNCHER"

if [[ -n "${DDB_DESKTOP_DIR:-}" ]]; then
    DESKTOP_DIR="$DDB_DESKTOP_DIR"
elif command -v xdg-user-dir > /dev/null 2>&1; then
    DESKTOP_DIR="$(xdg-user-dir DESKTOP)"
else
    DESKTOP_DIR="$HOME/Desktop"
fi
mkdir -p "$DESKTOP_DIR"
DESKTOP_FILE="$DESKTOP_DIR/ddb.desktop"

# Ship the logo out to the freedesktop icon theme root so both the
# desktop-file lookup ("Icon=ddb" by name) and the app window (via
# QIcon.fromTheme) find it. 256×256 is the sweet spot for taskbar +
# app-menu rendering; the source PNG at 1020×1019 is inside the repo
# so we symlink to it rather than duplicate.
ICON_ROOT="$HOME/.local/share/icons/hicolor/256x256/apps"
mkdir -p "$ICON_ROOT"
ln -sf "$INSTALL_DIR/assets/ddb_logo.png" "$ICON_ROOT/ddb.png"

# Refresh the freedesktop icon cache so newly-added icons are seen by
# already-running desktop shells. Silent on cache missing or failure —
# on a headless / minimal DE this is a no-op.
if command -v gtk-update-icon-cache > /dev/null 2>&1; then
    gtk-update-icon-cache -q -t "$HOME/.local/share/icons/hicolor" \
        2>/dev/null || true
fi

cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=DDB — Drosophila vial tracker
GenericName=Vial tracker
Comment=Track Drosophila vials with QR-coded labels, printed on the QL-820NWB
Exec=$LAUNCHER
Icon=ddb
Categories=Science;
Terminal=false
StartupNotify=true
StartupWMClass=DDB
EOF

chmod +x "$DESKTOP_FILE"

# GNOME / KDE mark unknown .desktop files as "untrusted" and refuse to
# launch them until the user right-clicks → "Allow launching". Setting
# the metadata::trusted attribute skips that prompt. gio ships with
# glib on every desktop DE — silent-fail on headless.
if command -v gio > /dev/null 2>&1; then
    gio set "$DESKTOP_FILE" metadata::trusted true 2>/dev/null || true
fi

# Belt-and-braces: if desktop-file-validate is around, warn on spec
# violations so we notice them at install time, not at first click.
if command -v desktop-file-validate > /dev/null 2>&1; then
    desktop-file-validate "$DESKTOP_FILE" \
        || warn "desktop-file-validate flagged issues in $DESKTOP_FILE"
fi

log "desktop launcher: $DESKTOP_FILE"

# ─── done ───────────────────────────────────────────────────────────
cat <<EOF

────────────────────────────────────────────────────────────────────
 DDB installed under your account.
────────────────────────────────────────────────────────────────────
   Repo         : $INSTALL_DIR
   Conda env    : $CONDA_ENV
   Launcher     : $DESKTOP_FILE

 Launch DDB:
   • double-click the desktop icon, or
   • from a terminal:
         ddb-launcher            # activates env + runs the GUI
     or manually:
         conda activate $CONDA_ENV && ddb gui

 First-run checklist:
   1. If this tablet uses the shared /srv/ddb/ database, open
      $INSTALL_DIR/local_paths.json and set
          "active_profile": "shared_tablet"
   2. Run the migrations once against whichever DB you're pointing at:
          conda activate $CONDA_ENV
          cd $INSTALL_DIR
          alembic upgrade head
   3. The status bar bottom-right shows who you're signed in as
      (your Linux username). If it's blank / wrong, log out and
      back in — a fresh session picks up group membership changes.
────────────────────────────────────────────────────────────────────
EOF
