"""Tests for the payload-emission dedupe helper.

Covers the bug that motivated the helper's extraction: previously the
"last seen" timestamp was bumped on every frame regardless of whether
we emitted, so a QR held continuously in front of the camera would
suppress itself forever — re-scanning the same code after a flip never
re-fired `payload_decoded`. The fix only updates the timestamp on
actual emission.
"""

from __future__ import annotations

from ddb.gui.frame_grabber import dedupe_emissions


def test_first_sighting_emits() -> None:
    state: dict[str, float] = {}
    emitted = dedupe_emissions(["DDB:ABC12"], state, now=10.0, reset_after_s=1.5)
    assert emitted == ["DDB:ABC12"]
    assert state == {"DDB:ABC12": 10.0}


def test_continuous_sighting_re_emits_after_reset_window() -> None:
    """The actual bug-reproducer: simulate the QR being detected on
    every frame for 3 seconds. The first frame must emit; subsequent
    frames within the window must NOT emit; the first frame past the
    window must emit again."""
    state: dict[str, float] = {}
    payload = ["DDB:ABC12"]
    reset = 1.5

    emitted_first = dedupe_emissions(payload, state, now=0.0, reset_after_s=reset)
    assert emitted_first == ["DDB:ABC12"]

    # Within the dedup window — even though we keep "seeing" it.
    for t in (0.03, 0.5, 1.0, 1.4):
        assert dedupe_emissions(payload, state, now=t, reset_after_s=reset) == []

    # First frame past the window — must re-emit, even though the QR
    # was continuously visible.
    emitted_late = dedupe_emissions(payload, state, now=1.6, reset_after_s=reset)
    assert emitted_late == ["DDB:ABC12"]
    assert state["DDB:ABC12"] == 1.6


def test_dedupe_window_is_independent_per_payload() -> None:
    state: dict[str, float] = {}
    reset = 1.5

    dedupe_emissions(["DDB:AAAAA"], state, now=0.0, reset_after_s=reset)
    # Different payload moments later — must emit on first sighting.
    emitted = dedupe_emissions(["DDB:BBBBB"], state, now=0.1, reset_after_s=reset)
    assert emitted == ["DDB:BBBBB"]


def test_stale_entries_drop_when_payload_disappears() -> None:
    """If a payload isn't seen again past the reset window, its entry
    should be cleaned up so the dict doesn't grow unbounded over a long
    scanning session."""
    state: dict[str, float] = {}
    dedupe_emissions(["DDB:GONE0"], state, now=0.0, reset_after_s=1.5)
    assert "DDB:GONE0" in state

    # Later iteration where GONE0 isn't in the decoded frame anymore.
    dedupe_emissions(["DDB:OTHER"], state, now=10.0, reset_after_s=1.5)
    assert "DDB:GONE0" not in state
    assert state["DDB:OTHER"] == 10.0
