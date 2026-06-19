"""Unit tests for `check_printer_or_ask`.

The helper is a thin wrapper around `shared_monitor` + `ensure_printer_or_ask`.
We monkeypatch both so the test runs without opening a modal dialog or
needing a live printer status.
"""

from __future__ import annotations

from ddb.gui.dialogs import printer_reconnect
from ddb.gui.dialogs.printer_reconnect import ReconnectChoice, check_printer_or_ask


class _FakeMonitor:
    def __init__(self, status) -> None:
        self.last_status = status


def test_check_printer_or_ask_returns_proceed_when_monitor_absent(monkeypatch):
    monkeypatch.setattr(printer_reconnect, "shared_monitor", lambda: None)
    monkeypatch.setattr(
        printer_reconnect,
        "ensure_printer_or_ask",
        lambda parent, status: (_ for _ in ()).throw(
            AssertionError("ensure_printer_or_ask should not be called")
        ),
    )
    assert check_printer_or_ask(None) is ReconnectChoice.PROCEED


def test_check_printer_or_ask_forwards_to_ensure_when_monitor_present(monkeypatch):
    captured = {}

    def _fake_ensure(parent, status):
        captured["parent"] = parent
        captured["status"] = status
        return ReconnectChoice.SKIP

    sentinel_status = object()
    monkeypatch.setattr(printer_reconnect, "shared_monitor", lambda: _FakeMonitor(sentinel_status))
    monkeypatch.setattr(printer_reconnect, "ensure_printer_or_ask", _fake_ensure)

    result = check_printer_or_ask("fake-parent")
    assert result is ReconnectChoice.SKIP
    assert captured["parent"] == "fake-parent"
    assert captured["status"] is sentinel_status
