"""Blocking BlueZ recovery actions — no Qt, no threads.

The GUI's printer-reconnect dialog used to own subprocess orchestration,
daemon probing, rfkill escalation, and Qt state management in one
class. That made the logic hard to test (you needed a live BlueZ +
display server) and near-impossible to exercise in CI.

This module is the extraction: each function is synchronous, returns
a plain result dataclass, and never touches Qt. Call from a worker
thread if blocking matters; unit-test with a simple `subprocess.run`
monkeypatch.
"""

from __future__ import annotations

import subprocess
import sys
import time
from dataclasses import dataclass

from ddb.printing._sidecars import sidecar_path


@dataclass(frozen=True)
class DaemonHealth:
    """Outcome of a single `bluetoothctl list` probe."""

    ok: bool
    detail: str


@dataclass(frozen=True)
class ActionResult:
    """Outcome of a recovery action (bond removal, service restart).

    `message` is a short human-readable tail suitable for display in
    the reconnect dialog; prefer `ok` for branching and `message` for
    rendering.
    """

    ok: bool
    message: str


# ----------------------------------------------------------------------
# Daemon health
# ----------------------------------------------------------------------


def probe_daemon(timeout_s: float = 4.0) -> DaemonHealth:
    """One-shot health probe. Returns ok=True iff `bluetoothctl list`
    exits cleanly and doesn't print the 'Waiting to connect to
    bluetoothd' / dbus-assertion signatures we've seen when the daemon
    is wedged."""
    try:
        p = subprocess.run(
            ["bluetoothctl", "list"],
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return DaemonHealth(False, "bluetoothctl timed out")
    combined = p.stdout + p.stderr
    if p.returncode == 0 and "Waiting to connect" not in combined:
        return DaemonHealth(True, combined.strip())
    return DaemonHealth(False, f"rc={p.returncode}: {combined.strip()[-200:]}")


def wait_for_daemon(seconds: float = 8.0) -> DaemonHealth:
    """Poll `probe_daemon` every 500 ms until ok, or until `seconds`
    elapses. Returns the last observed health so the caller can surface
    the actual failure instead of a generic timeout."""
    last = DaemonHealth(False, "no probe yet")
    steps = int(seconds * 2)
    for _ in range(steps):
        time.sleep(0.5)
        last = probe_daemon()
        if last.ok:
            # Normalise the success message so the UI shows the same
            # string regardless of which `bluetoothctl list` output
            # we happened to catch.
            return DaemonHealth(True, "bluetoothd is accepting connections.")
    return last


def looks_like_daemon_wedged(tail: str) -> bool:
    """Heuristic for the 'bluetoothd is wedged' signature in an error
    tail. Triggers on (a) our remove-bond sidecar exiting with rc=6
    (the health-check line), or (b) bluetoothctl itself printing
    'Waiting to connect to bluetoothd' / a dbus assertion before
    crashing. In both cases the cure is restarting the service."""
    t = tail.lower()
    return (
        "not accepting connections" in t
        or "waiting to connect to bluetoothd" in t
        or ("dbus" in t and "assertion" in t)
    )


# ----------------------------------------------------------------------
# Bond removal
# ----------------------------------------------------------------------


def remove_bond(mac: str, *, timeout_s: float = 30.0) -> ActionResult:
    """Run the bond-removal sidecar and translate its exit code.

    Exit code 0 → success; message = sidecar stdout. Anything else
    is a failure; message = sidecar stderr, which includes the
    'daemon wedged' diagnostic when rc=6 (see `_sidecars/bt_remove_bond.py`).
    """
    try:
        proc = subprocess.run(
            [sys.executable, str(sidecar_path("bt_remove_bond.py")), mac],
            capture_output=True,
            timeout=timeout_s,
            check=False,
        )
    except Exception as e:  # noqa: BLE001 — surface any subprocess failure to the dialog
        return ActionResult(False, f"{type(e).__name__}: {e}")

    if proc.returncode == 0:
        return ActionResult(True, proc.stdout.decode(errors="replace")[-400:])
    return ActionResult(False, proc.stderr.decode(errors="replace")[-800:])


# ----------------------------------------------------------------------
# Service restart (with rfkill escalation)
# ----------------------------------------------------------------------


def _run_systemctl() -> subprocess.CompletedProcess[bytes]:
    """Plain `systemctl restart bluetooth`. Uses systemd's polkit action
    (`auth_admin_keep`), which caches the password for ~5 min via the
    desktop's polkit agent."""
    return subprocess.run(
        ["systemctl", "restart", "bluetooth"],
        capture_output=True,
        timeout=30,
        check=False,
    )


def _run_pkexec() -> subprocess.CompletedProcess[bytes]:
    """pkexec fallback for sessions without a polkit agent (e.g. SSH).
    Re-prompts every call because pkexec's own action is plain
    `auth_admin`."""
    return subprocess.run(
        ["pkexec", "systemctl", "restart", "bluetooth"],
        capture_output=True,
        timeout=60,
        check=False,
    )


def _rfkill_cycle() -> bool:
    """Kernel-level kick for a wedged HCI controller. `rfkill` is
    usually group-accessible on Ubuntu (no sudo prompt), so this adds
    no extra friction. Returns False if `rfkill` isn't installed or
    the command raised — the caller then reports the original failure."""
    try:
        subprocess.run(["rfkill", "block", "bluetooth"], timeout=5, check=False)
        time.sleep(1.0)
        subprocess.run(["rfkill", "unblock", "bluetooth"], timeout=5, check=False)
    except FileNotFoundError:
        return False
    except Exception:  # noqa: BLE001 — best-effort nudge, never raise
        return False
    return True


def restart_service() -> ActionResult:
    """Restart bluetoothd, then escalate to rfkill if the daemon still
    refuses connections — the signature we've seen after an HCI-level
    hardware failure, where a software service restart alone doesn't
    unwedge the controller."""
    try:
        proc = _run_systemctl()
        out = (proc.stdout + proc.stderr).decode(errors="replace")
        if proc.returncode != 0 and (
            "Interactive authentication required" in out
            or "Failed to connect to bus" in out
        ):
            proc = _run_pkexec()
            out = (proc.stdout + proc.stderr).decode(errors="replace")
        if proc.returncode != 0:
            return ActionResult(False, out.strip()[-400:])

        h = wait_for_daemon(seconds=8.0)
        if h.ok:
            return ActionResult(True, h.detail)

        # systemctl returned OK but bluetoothctl still can't talk —
        # try a kernel-level HCI reset.
        if _rfkill_cycle():
            h2 = wait_for_daemon(seconds=6.0)
            if h2.ok:
                return ActionResult(True, "Recovered via rfkill cycle. " + h2.detail)
            return ActionResult(
                False,
                "systemctl restart + rfkill cycle both ran, but bluetoothctl "
                "still can't reach the daemon. Last probe: "
                + h2.detail
                + "\n\nPhysical fix: unplug and replug the Bluetooth adapter "
                "(or reboot).",
            )
        return ActionResult(
            False,
            "Service restarted but bluetoothctl still can't connect, and "
            "rfkill isn't available. Last probe: " + h.detail,
        )
    except FileNotFoundError as e:
        return ActionResult(
            False,
            f"{e.filename} not found — run this in a terminal instead:\n"
            "    sudo systemctl restart bluetooth",
        )
    except Exception as e:  # noqa: BLE001
        return ActionResult(False, f"{type(e).__name__}: {e}")
