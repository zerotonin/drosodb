"""Stand-alone sidecar scripts invoked via `subprocess.run`.

These files are not imported — they run in their own Python process so
that the caller can choose an interpreter (e.g. system Python for the
Bluetooth RFCOMM sender, which needs `AF_BLUETOOTH`; any Python for the
bond-removal helper, which only shells out to `bluetoothctl`).

Callers resolve a sidecar's on-disk path via `sidecar_path(name)` so
nobody hardcodes package-internal layout.
"""

from __future__ import annotations

from pathlib import Path

_DIR = Path(__file__).resolve().parent


def sidecar_path(name: str) -> Path:
    """Return the absolute path to a sidecar script by filename.

    Raises FileNotFoundError if the sidecar isn't where we expect, so a
    packaging regression surfaces loudly rather than as a cryptic
    `subprocess` error from inside a worker thread.
    """
    p = _DIR / name
    if not p.is_file():
        raise FileNotFoundError(f"sidecar not found: {p}")
    return p
