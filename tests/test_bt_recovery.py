"""Tests for `ddb.printing.bt_recovery`.

The module is pure: every external call goes through `subprocess.run`
or `time.sleep`. We monkeypatch both so the tests run in milliseconds
and work on any host, with or without BlueZ installed.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from ddb.printing import bt_recovery


@dataclass
class _FakeCompleted:
    returncode: int = 0
    stdout: str | bytes = b""
    stderr: str | bytes = b""


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """Never actually sleep — the polling loops call `time.sleep` which
    would make every test take seconds."""
    monkeypatch.setattr(bt_recovery.time, "sleep", lambda _s: None)


# ----------------------------------------------------------------------
# looks_like_daemon_wedged — pure string heuristic, no IO
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "tail",
    [
        "Waiting to connect to bluetoothd...",
        "bluetoothd on this host is not accepting connections",
        "dbus[123]: assertion \"connection != NULL\" failed",
    ],
)
def test_looks_like_daemon_wedged_matches(tail: str) -> None:
    assert bt_recovery.looks_like_daemon_wedged(tail)


@pytest.mark.parametrize(
    "tail",
    [
        "",
        "pair failed (expect idx=3)",
        "Device AC:4D:16:EB:B6:44 not available",
        "some unrelated error",
    ],
)
def test_looks_like_daemon_wedged_ignores_other_errors(tail: str) -> None:
    assert not bt_recovery.looks_like_daemon_wedged(tail)


# ----------------------------------------------------------------------
# probe_daemon
# ----------------------------------------------------------------------


def test_probe_daemon_ok(monkeypatch):
    monkeypatch.setattr(
        bt_recovery.subprocess,
        "run",
        lambda *a, **kw: _FakeCompleted(0, "Controller AA:BB:CC hostname [default]\n", ""),
    )
    h = bt_recovery.probe_daemon()
    assert h.ok is True
    assert "Controller" in h.detail


def test_probe_daemon_reports_waiting_signature(monkeypatch):
    monkeypatch.setattr(
        bt_recovery.subprocess,
        "run",
        lambda *a, **kw: _FakeCompleted(0, "Waiting to connect to bluetoothd...\n", ""),
    )
    h = bt_recovery.probe_daemon()
    assert h.ok is False


def test_probe_daemon_reports_nonzero_rc(monkeypatch):
    monkeypatch.setattr(
        bt_recovery.subprocess,
        "run",
        lambda *a, **kw: _FakeCompleted(1, "", "some error"),
    )
    h = bt_recovery.probe_daemon()
    assert h.ok is False
    assert "rc=1" in h.detail


def test_probe_daemon_handles_timeout(monkeypatch):
    def _raise(*a, **kw):
        raise bt_recovery.subprocess.TimeoutExpired(cmd="bluetoothctl", timeout=4)

    monkeypatch.setattr(bt_recovery.subprocess, "run", _raise)
    h = bt_recovery.probe_daemon()
    assert h.ok is False
    assert "timed out" in h.detail


# ----------------------------------------------------------------------
# wait_for_daemon
# ----------------------------------------------------------------------


def test_wait_for_daemon_returns_on_first_ok(monkeypatch):
    monkeypatch.setattr(
        bt_recovery,
        "probe_daemon",
        lambda *a, **kw: bt_recovery.DaemonHealth(True, "Controller..."),
    )
    h = bt_recovery.wait_for_daemon(seconds=8.0)
    assert h.ok is True


def test_wait_for_daemon_reports_last_failure(monkeypatch):
    monkeypatch.setattr(
        bt_recovery,
        "probe_daemon",
        lambda *a, **kw: bt_recovery.DaemonHealth(False, "rc=1: dead"),
    )
    h = bt_recovery.wait_for_daemon(seconds=1.0)  # 2 steps
    assert h.ok is False
    assert "dead" in h.detail


# ----------------------------------------------------------------------
# remove_bond
# ----------------------------------------------------------------------


def test_remove_bond_success(monkeypatch):
    monkeypatch.setattr(
        bt_recovery.subprocess,
        "run",
        lambda *a, **kw: _FakeCompleted(0, b"Device has been removed\n", b""),
    )
    r = bt_recovery.remove_bond("AC:4D:16:EB:B6:44")
    assert r.ok is True
    assert "Device has been removed" in r.message


def test_remove_bond_failure_propagates_stderr(monkeypatch):
    monkeypatch.setattr(
        bt_recovery.subprocess,
        "run",
        lambda *a, **kw: _FakeCompleted(6, b"", b"daemon wedged diagnostic here"),
    )
    r = bt_recovery.remove_bond("AC:4D:16:EB:B6:44")
    assert r.ok is False
    assert "daemon wedged" in r.message


def test_remove_bond_catches_subprocess_exception(monkeypatch):
    def _raise(*a, **kw):
        raise OSError("mocked boom")

    monkeypatch.setattr(bt_recovery.subprocess, "run", _raise)
    r = bt_recovery.remove_bond("AC:4D:16:EB:B6:44")
    assert r.ok is False
    assert "OSError" in r.message


# ----------------------------------------------------------------------
# restart_service — the multi-step escalation path
# ----------------------------------------------------------------------


def _queue_runs(*returns: _FakeCompleted):
    """Return a function suitable for `subprocess.run` that yields the
    queued results in order and fails loudly if called too many times."""
    it = iter(returns)

    def _run(*a, **kw):
        try:
            return next(it)
        except StopIteration:
            raise AssertionError("subprocess.run called more times than queued") from None

    return _run


def test_restart_service_happy_path(monkeypatch):
    # systemctl returns 0, wait_for_daemon sees the daemon come up.
    monkeypatch.setattr(bt_recovery, "_run_systemctl", lambda: _FakeCompleted(0, b"", b""))
    monkeypatch.setattr(
        bt_recovery,
        "wait_for_daemon",
        lambda seconds=8.0: bt_recovery.DaemonHealth(True, "bluetoothd is accepting connections."),
    )
    r = bt_recovery.restart_service()
    assert r.ok is True
    assert "accepting connections" in r.message


def test_restart_service_falls_back_to_pkexec_on_no_agent(monkeypatch):
    # First systemctl call says 'Interactive authentication required',
    # pkexec succeeds, then the daemon comes back up.
    monkeypatch.setattr(
        bt_recovery,
        "_run_systemctl",
        lambda: _FakeCompleted(1, b"", b"Interactive authentication required\n"),
    )
    called = {"pkexec": 0}

    def _pkexec():
        called["pkexec"] += 1
        return _FakeCompleted(0, b"", b"")

    monkeypatch.setattr(bt_recovery, "_run_pkexec", _pkexec)
    monkeypatch.setattr(
        bt_recovery, "wait_for_daemon", lambda seconds=8.0: bt_recovery.DaemonHealth(True, "up")
    )
    r = bt_recovery.restart_service()
    assert r.ok is True
    assert called["pkexec"] == 1


def test_restart_service_escalates_to_rfkill_when_daemon_still_wedged(monkeypatch):
    monkeypatch.setattr(bt_recovery, "_run_systemctl", lambda: _FakeCompleted(0, b"", b""))
    # First wait: still wedged. Second wait (after rfkill): up.
    waits = iter(
        [
            bt_recovery.DaemonHealth(False, "still wedged"),
            bt_recovery.DaemonHealth(True, "up after rfkill"),
        ]
    )
    monkeypatch.setattr(bt_recovery, "wait_for_daemon", lambda seconds=8.0: next(waits))
    monkeypatch.setattr(bt_recovery, "_rfkill_cycle", lambda: True)
    r = bt_recovery.restart_service()
    assert r.ok is True
    assert "rfkill" in r.message


def test_restart_service_reports_hardware_recovery_needed(monkeypatch):
    # systemctl OK, but daemon never comes back even after rfkill.
    monkeypatch.setattr(bt_recovery, "_run_systemctl", lambda: _FakeCompleted(0, b"", b""))
    monkeypatch.setattr(
        bt_recovery,
        "wait_for_daemon",
        lambda seconds=8.0: bt_recovery.DaemonHealth(False, "still wedged"),
    )
    monkeypatch.setattr(bt_recovery, "_rfkill_cycle", lambda: True)
    r = bt_recovery.restart_service()
    assert r.ok is False
    assert "unplug" in r.message.lower() or "reboot" in r.message.lower()


def test_restart_service_surfaces_unknown_error_cleanly(monkeypatch):
    def _raise():
        raise RuntimeError("mocked systemctl crash")

    monkeypatch.setattr(bt_recovery, "_run_systemctl", _raise)
    r = bt_recovery.restart_service()
    assert r.ok is False
    assert "RuntimeError" in r.message
