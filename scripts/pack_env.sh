#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────
#  DDB — pack the working `ddb` conda env into a portable tarball
#  « one download for the lab, N users install from it offline »
# ─────────────────────────────────────────────────────────────────────
#
# Run this ONCE on any account that has a working `ddb` conda env.
# The tarball can be handed to every other tablet user (or dropped at
# a shared location); install_user.sh detects it and skips the multi-
# hundred-MB conda download entirely.
#
# Motivation: the campus link (or per-IP throttle at conda.anaconda.org)
# comfortably supports one download at a time — running install_user.sh
# for user #2 while user #1 is still fetching packages drops the shared
# throughput to ~100 KB/s. The pack tarball is a one-time ~200 MB copy
# on the local disk, seconds instead of tens-of-minutes.
#
# Usage:
#     bash scripts/pack_env.sh
#
# Env-var overrides:
#     DDB_ENV_PACK_OUT       target path for the tarball
#                            (default: /srv/ddb/env-packs/ddb-env.tar.gz
#                             if writable, else /tmp/ddb-env.tar.gz)
#     DDB_CONDA_ENV          env name to pack (default: ddb)

set -euo pipefail

CONDA_ENV="${DDB_CONDA_ENV:-ddb}"

log()  { printf '\033[1;34m▸\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m✗\033[0m %s\n' "$*" >&2; exit 1; }

# ─── 1) Locate + activate conda ─────────────────────────────────────
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

CONDA_SH="$(find_conda_sh || true)"
[[ -z "$CONDA_SH" ]] && die "conda not found"

# shellcheck disable=SC1090
source "$CONDA_SH"

if ! conda env list | awk '{print $1}' | grep -qxF "$CONDA_ENV"; then
    die "conda env '$CONDA_ENV' does not exist — run install_user.sh first"
fi

conda activate "$CONDA_ENV"

# ─── 2) Install conda-pack if missing ───────────────────────────────
if ! command -v conda-pack > /dev/null 2>&1; then
    log "installing conda-pack from conda-forge (~5 MB, one-time)"
    conda install -c conda-forge conda-pack -y
fi

# ─── 3) Pick an output path — prefer the shared /srv location ───────
default_shared="/srv/ddb/env-packs/ddb-env.tar.gz"
default_local="/tmp/ddb-env.tar.gz"

if [[ -n "${DDB_ENV_PACK_OUT:-}" ]]; then
    OUT="$DDB_ENV_PACK_OUT"
elif [[ -d "$(dirname "$default_shared")" ]] && [[ -w "$(dirname "$default_shared")" ]]; then
    OUT="$default_shared"
else
    OUT="$default_local"
    if [[ -d "$(dirname "$default_shared")" ]]; then
        warn "$(dirname "$default_shared") exists but isn't writable —"
        warn "packing to $OUT. Move it to $default_shared afterwards"
        warn "so other tablet users' install_user.sh finds it automatically."
    fi
fi

mkdir -p "$(dirname "$OUT")"

# ─── 4) Pack ────────────────────────────────────────────────────────
if [[ -f "$OUT" ]]; then
    log "removing existing $OUT (conda-pack won't overwrite)"
    rm -f "$OUT"
fi

log "packing '$CONDA_ENV' → $OUT (this can take a couple of minutes)"
# --ignore-editable-packages: our `pip install -e .` gets re-run per
# user; the source repo lives at $INSTALL_DIR/, not inside the env.
# --ignore-missing-files: silence noise from packages that shipped a
# manifest referencing a file conda-forge removed later.
conda pack -n "$CONDA_ENV" -o "$OUT" \
    --ignore-editable-packages \
    --ignore-missing-files

chmod 644 "$OUT"
size=$(du -h "$OUT" | cut -f1)

cat <<EOF

──────────────────────────────────────────────────────────────────────
 Packed '$CONDA_ENV' → $OUT  ($size)
──────────────────────────────────────────────────────────────────────

 Other tablet users can now install DDB without downloading anything:

   bash ~/PyProject/drosodb/scripts/install_user.sh

 install_user.sh checks for the pack in this order:
   1. \$DDB_ENV_PACK       (explicit override)
   2. /srv/ddb/env-packs/ddb-env.tar.gz     (shared, this file)
   3. ~/ddb-env.tar.gz     (per-user fallback)
   4. Fresh conda/mamba install (network)

 If your pack landed in /tmp/, move it into place:
     sudo mkdir -p /srv/ddb/env-packs
     sudo cp $OUT /srv/ddb/env-packs/ddb-env.tar.gz
     sudo chmod 644 /srv/ddb/env-packs/ddb-env.tar.gz
──────────────────────────────────────────────────────────────────────
EOF
