"""Tests for the FlyBase refresh-decision function.

All inputs are explicit so the tests are just table entries — no clock,
no filesystem. The decision matrix covers: feature off, manual mode,
never-downloaded, fresh, stale, stale-but-postponed, stale-postpone-
expired.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from ddb.flybase import RefreshDecision, decide_refresh

_NOW = datetime(2026, 4, 24, 12, 0, tzinfo=UTC)


def _dec(**overrides):
    kwargs = dict(
        enabled=True,
        mode="weekly",
        catalog_downloaded_at=_NOW - timedelta(days=1),
        postponed_until=None,
        now=_NOW,
    )
    kwargs.update(overrides)
    return decide_refresh(**kwargs)


def test_disabled_feature_never_prompts() -> None:
    assert _dec(enabled=False) is RefreshDecision.NOTHING


def test_manual_mode_never_prompts_even_if_missing() -> None:
    assert _dec(mode="manual", catalog_downloaded_at=None) is RefreshDecision.NOTHING


def test_weekly_mode_missing_prompts() -> None:
    assert _dec(mode="weekly", catalog_downloaded_at=None) is RefreshDecision.PROMPT_MISSING


def test_weekly_mode_missing_respects_postpone() -> None:
    future = _NOW + timedelta(days=2)
    assert (
        _dec(mode="weekly", catalog_downloaded_at=None, postponed_until=future)
        is RefreshDecision.POSTPONED
    )


def test_weekly_fresh_catalog_quiet() -> None:
    assert _dec(catalog_downloaded_at=_NOW - timedelta(days=3)) is RefreshDecision.NOTHING


def test_weekly_just_over_threshold_prompts() -> None:
    assert _dec(catalog_downloaded_at=_NOW - timedelta(days=8)) is RefreshDecision.PROMPT_STALE


def test_weekly_stale_but_postponed_quiet() -> None:
    assert (
        _dec(
            catalog_downloaded_at=_NOW - timedelta(days=14),
            postponed_until=_NOW + timedelta(days=1),
        )
        is RefreshDecision.POSTPONED
    )


def test_weekly_stale_postpone_expired_prompts() -> None:
    assert (
        _dec(
            catalog_downloaded_at=_NOW - timedelta(days=14),
            postponed_until=_NOW - timedelta(hours=1),
        )
        is RefreshDecision.PROMPT_STALE
    )


def test_monthly_thirty_days_is_still_fresh() -> None:
    result = _dec(mode="monthly", catalog_downloaded_at=_NOW - timedelta(days=20))
    assert result is RefreshDecision.NOTHING


def test_monthly_thirty_one_days_is_stale() -> None:
    assert (
        _dec(mode="monthly", catalog_downloaded_at=_NOW - timedelta(days=31))
        is RefreshDecision.PROMPT_STALE
    )


def test_unknown_mode_is_treated_as_quiet() -> None:
    assert _dec(mode="quarterly") is RefreshDecision.NOTHING
