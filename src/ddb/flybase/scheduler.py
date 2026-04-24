"""Decide whether/how to prompt the user about catalog refresh.

Pure function: takes the current time, the catalog meta, the user's
refresh mode, and the postpone file state; returns an enum telling the
GUI what prompt (if any) to open.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum


class RefreshDecision(StrEnum):
    """What the startup check wants the GUI to do about the catalog."""

    NOTHING = "nothing"  # catalog is fresh, or feature is disabled
    PROMPT_MISSING = "prompt_missing"  # never downloaded — offer to get it
    PROMPT_STALE = "prompt_stale"  # older than the refresh threshold
    POSTPONED = "postponed"  # stale but user asked to snooze — stay quiet


@dataclass(frozen=True)
class _Inputs:
    enabled: bool
    mode: str  # "manual" | "weekly" | "monthly"
    catalog_downloaded_at: datetime | None
    postponed_until: datetime | None
    now: datetime


_THRESHOLDS: dict[str, timedelta] = {
    "weekly": timedelta(days=7),
    "monthly": timedelta(days=30),
}


def decide_refresh(
    *,
    enabled: bool,
    mode: str,
    catalog_downloaded_at: datetime | None,
    postponed_until: datetime | None,
    now: datetime,
) -> RefreshDecision:
    """See module docstring. All inputs are explicit so this is trivially
    unit-testable — no wall-clock reads, no filesystem access."""
    if not enabled:
        return RefreshDecision.NOTHING
    if mode == "manual":
        # Manual mode never prompts — user drives downloads via the
        # Settings button. Missing file is fine.
        return RefreshDecision.NOTHING

    # For weekly / monthly, missing always prompts regardless of postpone:
    # a never-downloaded catalog blocks the whole feature, so the user
    # should see the offer even on a busy day.
    if catalog_downloaded_at is None:
        if postponed_until is not None and postponed_until > now:
            return RefreshDecision.POSTPONED
        return RefreshDecision.PROMPT_MISSING

    threshold = _THRESHOLDS.get(mode)
    if threshold is None:
        # Unknown mode — treat as manual to avoid surprising prompts.
        return RefreshDecision.NOTHING

    age = now - catalog_downloaded_at
    if age < threshold:
        return RefreshDecision.NOTHING

    if postponed_until is not None and postponed_until > now:
        return RefreshDecision.POSTPONED
    return RefreshDecision.PROMPT_STALE
