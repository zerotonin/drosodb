"""Machine-specific path resolver.

`local_paths.json` at the repo root selects a named profile whose values
override the in-repo defaults for the shared data directory and DB URL.
Env vars (`DDB_DATABASE_URL`, `DDB_DATA_DIR`) still win — a single dev
can pin an override without editing the profile.

Priority per field:
    1. Env var  (DDB_DATABASE_URL / DDB_DATA_DIR)
    2. Active profile in local_paths.json
    3. In-repo fallback (`fallback` argument)

The file is optional. Missing / malformed JSON → treat like a single-user
install and use fallbacks. That keeps first-run onboarding painless; the
setup script for the shared-tablet mode is opt-in.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

_PKG_ROOT = Path(__file__).resolve().parent.parent.parent
_LOCAL_PATHS = _PKG_ROOT / "local_paths.json"


def _load_active_profile() -> dict[str, Any]:
    """Return the active profile dict, or {} on any failure.

    Silent-on-error is deliberate: an install with no local_paths.json is
    the normal single-user case, and a broken file shouldn't wedge the
    whole app before it can even show an error dialog. The values inside
    the profile are still typed at the call site (str for URL, Path for
    dir), so a garbage value fails loudly at first use.
    """
    if not _LOCAL_PATHS.exists():
        return {}
    try:
        blob = json.loads(_LOCAL_PATHS.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    profiles = blob.get("profiles") or {}
    active = blob.get("active_profile") or "local"
    profile = profiles.get(active)
    return profile if isinstance(profile, dict) else {}


def resolve_database_url(fallback: str) -> str:
    """Return the DB URL to use for this run."""
    env = os.environ.get("DDB_DATABASE_URL")
    if env:
        return env
    profile_val = _load_active_profile().get("database_url")
    if profile_val:
        return str(profile_val)
    return fallback


def resolve_data_dir(fallback: Path) -> Path:
    """Return the data directory to use for this run."""
    env = os.environ.get("DDB_DATA_DIR")
    if env:
        return Path(env)
    profile_val = _load_active_profile().get("data_root")
    if profile_val:
        return Path(str(profile_val))
    return fallback


def active_profile_name() -> str:
    """Return the currently-selected profile name for status-bar display."""
    if not _LOCAL_PATHS.exists():
        return "default"
    try:
        blob = json.loads(_LOCAL_PATHS.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "default"
    return str(blob.get("active_profile") or "local")
