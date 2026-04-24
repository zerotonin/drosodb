#!/usr/bin/env python3
"""Drop a stored BlueZ bond for a given BD_ADDR.

Invoked by the Printer Reconnect dialog. The QL-820NWB accepts RFCOMM
SPP without pairing, so a stale bond whose link key drifted from the
printer's key is the usual cause of EACCES on connect. `bluetoothctl
remove` clears it; the next `connect()` goes in unauthenticated.

Before acting, probes bluetoothd liveness — a wedged daemon (after an
earlier bluetoothctl crash) refuses DBus clients, and exit code 6
lets the UI offer 'Restart BT service' rather than chasing a cryptic
downstream error.

Usage (also runnable by hand):

    python3 bt_remove_bond.py <MAC>

Exit codes (the caller relies on these; don't renumber without updating
`gui/dialogs/printer_reconnect.py`):

    0   bond removed or was already absent
    6   bluetoothd is wedged / not accepting connections
    7   bluetoothctl remove timed out
    *   propagated from bluetoothctl on a real failure
"""

from __future__ import annotations

import subprocess
import sys
import time


def probe_bluetoothd() -> tuple[bool, object]:
    """Return (ok, diagnostic).

    A freshly-restarted bluetoothd can take 1-2 s before it accepts
    DBus clients, so we retry a few times before concluding it's
    wedged.
    """
    last: object = None
    for _ in range(5):
        try:
            p = subprocess.run(
                ["bluetoothctl", "list"],
                capture_output=True,
                text=True,
                timeout=4,
            )
        except subprocess.TimeoutExpired as e:
            last = ("TIMEOUT", str(e), None)
            time.sleep(1.0)
            continue
        combined = p.stdout + p.stderr
        if (
            p.returncode == 0
            and "Waiting to connect to bluetoothd" not in combined
            and "assertion" not in combined.lower()
        ):
            return True, combined
        last = (p.stdout, p.stderr, p.returncode)
        time.sleep(1.0)
    return False, last


def main(argv: list[str]) -> int:
    mac = argv[1]

    ok, info = probe_bluetoothd()
    if not ok:
        sys.stderr.write(
            "\nbluetoothd on this host is not accepting connections "
            "(wedged after an earlier bluetoothctl crash).\n"
            "Click 'Restart BT service' in the dialog, or run "
            "'sudo systemctl restart bluetooth' in a terminal, then retry.\n"
            "\n--- diagnostic ---\n"
        )
        if isinstance(info, tuple):
            sys.stderr.write(f"bluetoothctl probe returncode={info[2]!r}\n")
            sys.stderr.write(f"stdout: {info[0]!r}"[:400] + "\n")
            sys.stderr.write(f"stderr: {info[1]!r}"[:400] + "\n")
        return 6

    # Drop the bond. "Device does not exist" / "not available" are
    # already the desired end state — treat them as success.
    try:
        proc = subprocess.run(
            ["bluetoothctl", "remove", mac],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except subprocess.TimeoutExpired:
        sys.stderr.write("\nbluetoothctl remove timed out\n")
        return 7

    combined = proc.stdout + proc.stderr
    if (
        proc.returncode == 0
        or "Device has been removed" in combined
        or "Device does not exist" in combined
        or "not available" in combined
    ):
        sys.stdout.write(combined)
        return 0

    sys.stderr.write(combined)
    return proc.returncode or 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
