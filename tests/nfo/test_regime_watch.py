"""Pure-math helpers inside `scripts/nfo/regime_watch.py`.

Only the network-free utilities are under test here — the `evaluate()` entry
point requires Dhan and is exercised by integration runs, not unit tests.
"""
from __future__ import annotations

import math

import pytest

import regime_watch as rw


# ── _vix_percentile / _vix_value_at_percentile round-trip ───────────────────


def test_vix_percentile_empty_history_is_zero() -> None:
    # Guard branch — an empty VIX history returns 0.0 rather than NaN so the
    # downstream signal just fails cleanly rather than blowing up on NaN math.
    assert rw._vix_percentile(20.0, []) == 0.0


def test_vix_percentile_is_ecdf_at_each_sample() -> None:
    # Ordered integer series — the ECDF steps up by 1/n at each observation.
    hist = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert rw._vix_percentile(1.0, hist) == pytest.approx(0.2)
    assert rw._vix_percentile(2.5, hist) == pytest.approx(0.4)
    assert rw._vix_percentile(5.0, hist) == pytest.approx(1.0)
    # Past the max — pegs at 1.0 (not > 1.0, not wrapped).
    assert rw._vix_percentile(999.0, hist) == pytest.approx(1.0)


def test_vix_value_at_percentile_is_inverse_of_vix_percentile() -> None:
    # This is the core contract: `_vix_value_at_percentile` should return a v
    # such that `_vix_percentile(v, hist) ≥ pct`, AND no smaller observation
    # in `hist` would satisfy the same inequality. Pre-fix, n=10 pct=0.7 picked
    # the 8th-smallest (ECDF at 0.8), overstating the threshold.
    hist = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
    v = rw._vix_value_at_percentile(hist, 0.7)
    assert v == 7.0                                       # 7th smallest, not 8th
    assert rw._vix_percentile(v, hist) >= 0.7
    # The previous observation (6.0) would NOT satisfy the ≥ 0.7 bound.
    assert rw._vix_percentile(6.0, hist) < 0.7


def test_vix_value_at_percentile_lower_bound() -> None:
    # pct=0 picks the smallest observation, not index −1. Guards the clamp on
    # the `ceil(0 * n) − 1 = −1` edge case.
    hist = [1.0, 2.0, 3.0]
    assert rw._vix_value_at_percentile(hist, 0.0) == 1.0


def test_vix_value_at_percentile_upper_bound() -> None:
    # pct=1.0 picks the max — the ECDF first reaches 1.0 at the last sorted obs.
    hist = [1.0, 2.0, 3.0]
    assert rw._vix_value_at_percentile(hist, 1.0) == 3.0


def test_vix_value_at_percentile_non_integer_product() -> None:
    # n=7, pct=0.5 → ceil(3.5) − 1 = 3 → 4th smallest. Smallest v with ECDF ≥ 0.5.
    hist = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0]
    v = rw._vix_value_at_percentile(hist, 0.5)
    assert v == 40.0
    assert rw._vix_percentile(v, hist) >= 0.5


def test_vix_value_at_percentile_empty_is_zero() -> None:
    assert rw._vix_value_at_percentile([], 0.7) == 0.0


# ── _vix_series_with_live ───────────────────────────────────────────────────


def test_vix_series_with_live_appends_when_available() -> None:
    # Typical market-hours path: daily series ends yesterday; live print is
    # today's latest intraday VIX. Downstream signals that key off s[-1]
    # (iv_rank / iv_percentile / term_structure EMAs) need today's value, not
    # yesterday's. Appending makes the last element "today."
    series = [14.0, 15.0, 16.0]
    out = rw._vix_series_with_live(series, 18.5)
    assert out == [14.0, 15.0, 16.0, 18.5]
    assert out is not series      # must not mutate the caller's list


def test_vix_series_with_live_passes_through_when_no_live() -> None:
    # Outside market hours or on Dhan gap, vix_live is None — the series is
    # already fresh (it contains today's close), no append needed.
    series = [14.0, 15.0, 16.0]
    out = rw._vix_series_with_live(series, None)
    assert out == series
    assert out is not series


def test_vix_series_with_live_rejects_nan() -> None:
    # A NaN intraday print would corrupt EMA calculations silently. We treat
    # it the same as "no live print" — pass the series through unchanged.
    series = [14.0, 15.0]
    out = rw._vix_series_with_live(series, float("nan"))
    assert out == series


def test_vix_series_with_live_empty_history_still_appends() -> None:
    # A fresh deployment with no VIX history yet — we still want today's live
    # print to seed downstream signals (they self-degrade to NaN on < slow-period).
    out = rw._vix_series_with_live([], 19.3)
    assert out == [19.3]
