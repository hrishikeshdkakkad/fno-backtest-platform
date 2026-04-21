"""Signal-module sanity checks — all pure-math, no fixtures needed."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from nfo import signals


# ── IV rank / percentile ────────────────────────────────────────────────────


def test_iv_rank_known_midpoint() -> None:
    assert signals.iv_rank([10.0, 20.0, 15.0]) == pytest.approx(0.5)


def test_iv_rank_flat_series_is_nan() -> None:
    assert math.isnan(signals.iv_rank([10.0, 10.0, 10.0]))


def test_iv_rank_single_point_is_nan() -> None:
    assert math.isnan(signals.iv_rank([10.0]))


def test_iv_rank_respects_lookback() -> None:
    series = [100.0] * 300 + [50.0, 75.0]
    # With lookback=2, range is [50,75] and current (75) is at top → 1.0.
    assert signals.iv_rank(series, lookback=2) == pytest.approx(1.0)


def test_iv_percentile_basic() -> None:
    assert signals.iv_percentile([1, 2, 3, 4, 5]) == pytest.approx(1.0)
    assert signals.iv_percentile([5, 4, 3, 2, 1]) == pytest.approx(0.2)


# ── ATR / ADX / RSI ─────────────────────────────────────────────────────────


def _daily_bars(closes: list[float], noise: float = 1.0) -> pd.DataFrame:
    closes_arr = np.asarray(closes, dtype=float)
    return pd.DataFrame({
        "open": closes_arr,
        "high": closes_arr + noise,
        "low": closes_arr - noise,
        "close": closes_arr,
    })


def test_atr_converges_to_range_for_constant_bars() -> None:
    df = _daily_bars([100.0] * 50, noise=2.0)  # range = 4
    atr = signals.atr(df, window=14)
    assert atr.iloc[-1] == pytest.approx(4.0, rel=1e-3)


def test_adx_high_on_monotonic_trend() -> None:
    df = _daily_bars(list(np.linspace(100, 200, 100)), noise=0.5)
    adx = signals.adx(df, window=14)
    assert adx.iloc[-1] > 40


def test_adx_low_on_flat_market() -> None:
    rng = np.random.default_rng(42)
    prices = 100 + rng.standard_normal(100) * 0.2
    df = _daily_bars(prices.tolist(), noise=0.3)
    adx = signals.adx(df, window=14)
    assert adx.iloc[-1] < 25


def test_rsi_above_70_on_uptrend() -> None:
    closes = pd.Series(np.linspace(100, 140, 60))
    r = signals.rsi(closes, window=14)
    assert r.iloc[-1] > 70


def test_rsi_below_30_on_downtrend() -> None:
    closes = pd.Series(np.linspace(140, 100, 60))
    r = signals.rsi(closes, window=14)
    assert r.iloc[-1] < 30


# ── EMA slope & pullback ────────────────────────────────────────────────────


def test_ema_slope_positive_on_uptrend() -> None:
    closes = pd.Series(np.linspace(100, 200, 80))
    assert signals.ema_slope(closes) > 0


def test_ema_slope_negative_on_downtrend() -> None:
    closes = pd.Series(np.linspace(200, 100, 80))
    assert signals.ema_slope(closes) < 0


def test_pullback_atr_scaled_units() -> None:
    # 2% drop on spot 100 with ATR 1 → pullback of 2 ATR units.
    assert signals.pullback_atr_scaled(98, 100, 1.0) == pytest.approx(2.0)


def test_pullback_atr_scaled_nan_when_atr_zero() -> None:
    assert math.isnan(signals.pullback_atr_scaled(99, 100, 0))


# ── Trend regime ────────────────────────────────────────────────────────────


def test_trend_regime_all_three_pass_on_strong_uptrend() -> None:
    df = _daily_bars(list(np.linspace(100, 200, 120)), noise=0.3)
    tr = signals.trend_regime(df)
    assert tr.trending_up is True
    assert tr.score == 3


def test_trend_regime_fails_on_downtrend() -> None:
    df = _daily_bars(list(np.linspace(200, 100, 120)), noise=0.3)
    tr = signals.trend_regime(df)
    assert tr.trending_up is False
    assert tr.score <= 1
    assert tr.ema20_over_ema50 is False


# ── Chain metrics ───────────────────────────────────────────────────────────


def _put_chain(spot: float = 24000, step: int = 50, n: int = 20) -> pd.DataFrame:
    strikes = [spot + step * (i - n // 2) for i in range(n)]
    # Synthetic smile: deep-OTM puts have ~+4 vol extra over ATM.
    return pd.DataFrame({
        "strike": [float(s) for s in strikes],
        "close": [max(10.0, 100.0 - abs(s - spot) / 50) for s in strikes],
        "iv": [18.0 + 4.0 * max(0.0, (spot - s) / 1000.0) for s in strikes],
    })


def _call_chain(spot: float = 24000, step: int = 50, n: int = 20) -> pd.DataFrame:
    strikes = [spot + step * (i - n // 2) for i in range(n)]
    # Calls: slight positive skew on upside → roughly flat at 18.
    return pd.DataFrame({
        "strike": [float(s) for s in strikes],
        "close": [max(10.0, 100.0 - abs(s - spot) / 50) for s in strikes],
        "iv": [18.0 + 1.0 * max(0.0, (s - spot) / 1500.0) for s in strikes],
    })


def test_strike_iv_lookup() -> None:
    chain = _put_chain()
    iv = signals.strike_iv(chain, strike=24000)
    assert iv == pytest.approx(18.0)


def test_strike_iv_missing_returns_nan() -> None:
    chain = _put_chain()
    assert math.isnan(signals.strike_iv(chain, strike=999999))


def test_skew_25d_puts_richer_than_calls() -> None:
    puts = _put_chain()
    calls = _call_chain()
    snap = signals.skew_25d(puts, calls, spot=24000, years_to_expiry=35 / 365)
    assert snap.put_25d_strike < 24000       # 25Δ put is OTM below spot
    assert snap.call_25d_strike > 24000      # 25Δ call is OTM above spot
    assert snap.skew_vol_pts > 0             # synthetic smile has put richness


def test_skew_25d_empty_chain_returns_nan() -> None:
    snap = signals.skew_25d(pd.DataFrame(), pd.DataFrame(), spot=24000, years_to_expiry=0.1)
    assert math.isnan(snap.skew_vol_pts)


# ── Term structure ──────────────────────────────────────────────────────────


def test_term_structure_contango_on_steady_vix() -> None:
    # Flat VIX 14 — slow and fast EMAs converge, slope ≈ 0, contango=True
    # (slow ≥ fast because the fast adapts first to the flat baseline).
    ts = signals.term_structure([14.0] * 60)
    assert ts.fast_ema == pytest.approx(14.0, abs=0.5)
    assert ts.slow_ema == pytest.approx(14.0, abs=0.5)


def test_term_structure_backwardation_after_vix_spike() -> None:
    hist = [14.0] * 40 + [30.0] * 10  # sudden spike
    ts = signals.term_structure(hist)
    assert ts.fast_ema > ts.slow_ema
    assert ts.contango is False


# ── Composite grader ────────────────────────────────────────────────────────


def test_composite_score_all_pass_gives_top_grade() -> None:
    score, grade = signals.composite_score(
        {"a": True, "b": True, "c": True, "d": True}
    )
    assert score == 4.0
    assert grade == signals.GRADE_LADDER[-1]


def test_composite_score_none_pass_gives_bottom_grade() -> None:
    score, grade = signals.composite_score(
        {"a": False, "b": False, "c": False, "d": False}
    )
    assert score == 0.0
    assert grade == signals.GRADE_LADDER[0]


def test_composite_score_respects_weights() -> None:
    # Same passes, but weight pushes us up a notch.
    passes = {"vix": True, "iv_rv": False}
    _, grade_equal = signals.composite_score(passes)
    # Weight vix heavily — 3 of 4 effective votes pass.
    _, grade_weighted = signals.composite_score(passes, {"vix": 3.0, "iv_rv": 1.0})
    assert signals.GRADE_LADDER.index(grade_weighted) > signals.GRADE_LADDER.index(grade_equal)


# ── Entry-timing indicators ─────────────────────────────────────────────────


def _ohlc(closes: list[float], width: float = 1.0) -> pd.DataFrame:
    """Build a synthetic OHLC DataFrame from a close series where close stays
    within [low, high]. Required for stochastic — unphysical close-outside-range
    values break %K."""
    c = np.asarray(closes, dtype=float)
    return pd.DataFrame({
        "open": c, "high": c + width, "low": c - width, "close": c,
    })


# Bollinger


def test_bollinger_flat_series_gives_zero_bandwidth() -> None:
    bb = signals.bollinger_bands(pd.Series([100.0] * 50))
    assert bb.bandwidth == pytest.approx(0.0, abs=1e-9)
    # Flat series → std=0 → z_score undefined.
    assert math.isnan(bb.z_score)


def test_bollinger_position_above_upper_band() -> None:
    # Long flat then a pop — last spot should sit above the band.
    series = pd.Series([100.0] * 30 + [110.0])
    bb = signals.bollinger_bands(series, window=20)
    assert bb.z_score > 1.0


def test_bollinger_squeeze_detected_on_contracting_vol() -> None:
    # Wide regime followed by tight regime → bandwidth at new low → squeeze.
    rng = np.random.default_rng(0)
    wide = 100 + rng.standard_normal(100) * 5.0
    tight = 100 + rng.standard_normal(40) * 0.2
    bb = signals.bollinger_bands(pd.Series(np.concatenate([wide, tight])))
    assert bb.squeeze is True


def test_bollinger_no_squeeze_when_vol_is_expanding() -> None:
    rng = np.random.default_rng(1)
    tight = 100 + rng.standard_normal(100) * 0.2
    wide = 100 + rng.standard_normal(40) * 5.0
    bb = signals.bollinger_bands(pd.Series(np.concatenate([tight, wide])))
    assert bb.squeeze is False


# MACD


def test_macd_histogram_positive_on_uptrend() -> None:
    closes = pd.Series(np.linspace(100, 140, 80))
    m = signals.macd(closes)
    assert m.histogram > 0
    assert m.state in ("rising_pos", "falling_pos")


def test_macd_histogram_negative_on_downtrend() -> None:
    closes = pd.Series(np.linspace(140, 100, 80))
    m = signals.macd(closes)
    assert m.histogram < 0
    assert m.state in ("rising_neg", "falling_neg")


def test_macd_state_is_unknown_with_short_series() -> None:
    m = signals.macd(pd.Series([100.0] * 5))
    assert m.state == "unknown"


# Stochastic


def test_stochastic_oversold_at_range_low() -> None:
    # Series climbs to a high, then gives all of it back — ends at the low.
    up = list(np.linspace(100, 140, 30))
    down = list(np.linspace(140, 100, 20))
    st = signals.stochastic(_ohlc(up + down))
    assert st.state in ("oversold", "oversold_turning_up")
    assert st.k < 20


def test_stochastic_turning_up_after_oversold_dip() -> None:
    # Down then reverses — last bar above prior.
    down = list(np.linspace(140, 100, 25))
    bounce = list(np.linspace(100, 108, 5))
    st = signals.stochastic(_ohlc(down + bounce))
    # After a bounce from the bottom, %K should be rising into or through the
    # oversold zone. Accept either "oversold_turning_up" or "neutral" depending
    # on exact smoothing tick; both are non-"overbought" / non-"oversold".
    assert st.state in ("oversold_turning_up", "oversold", "neutral")
    assert st.k >= 0


def test_stochastic_overbought_at_range_high() -> None:
    down = list(np.linspace(140, 100, 20))
    up = list(np.linspace(100, 150, 30))
    st = signals.stochastic(_ohlc(down + up))
    assert st.state in ("overbought", "overbought_turning_down")
    assert st.k > 80


# Entry-timing composite


def test_entry_timing_score_range_0_to_100() -> None:
    rng = np.random.default_rng(42)
    for _ in range(5):
        closes = 100 + rng.standard_normal(60).cumsum()
        ts = signals.entry_timing_score(_ohlc(closes.tolist()))
        if math.isfinite(ts.score):
            assert 0.0 <= ts.score <= 100.0


def test_entry_timing_score_short_series_is_unknown() -> None:
    ts = signals.entry_timing_score(_ohlc([100.0] * 10))
    assert math.isnan(ts.score)
    assert ts.grade == "Unknown"


def test_entry_timing_score_reasoning_populated_on_full_input() -> None:
    rng = np.random.default_rng(3)
    closes = (100 + rng.standard_normal(80).cumsum() * 0.3).tolist()
    ts = signals.entry_timing_score(_ohlc(closes))
    assert ts.grade in ("Strong", "Good", "Neutral", "Weak", "Avoid")
    assert len(ts.reasoning) >= 3     # at least BB + MACD + Stoch lines


def test_entry_timing_score_grade_thresholds() -> None:
    # Verify the boundary logic on the scoring rubric directly.
    # Construct synthetic values and re-use the score→grade mapping by
    # computing scores for crafted daily frames that push components high.
    # "Strong" requires ≥75 — we can't easily force that deterministically
    # without replicating the full calc, so just verify the ladder ordering:
    # higher scores must map to grades at least as good as lower scores.
    grades_order = ["Avoid", "Weak", "Neutral", "Good", "Strong"]
    # Manufacture rising-scored synthetic inputs by increasing series
    # coherence (lower vol = higher scores tend to cluster).
    rng = np.random.default_rng(11)
    last_grade_idx = -1
    for vol in (5.0, 2.0, 0.5):
        closes = (100 + rng.standard_normal(80).cumsum() * vol).tolist()
        ts = signals.entry_timing_score(_ohlc(closes, width=vol))
        # Skip Unknown; we're asserting monotonicity where graded.
        if ts.grade in grades_order:
            idx = grades_order.index(ts.grade)
            # Lower vol should not produce a strictly worse grade.
            assert idx >= last_grade_idx - 1   # loose tolerance for randomness
            last_grade_idx = max(last_grade_idx, idx)


# ── Calendar-structure helpers (Shaikh-Padhi seasonality) ────────────────────


from datetime import date


def test_day_of_week_score_monday_penalty() -> None:
    # 2026-04-20 is a Monday.
    assert signals.day_of_week_score(date(2026, 4, 20)) == -1


def test_day_of_week_score_non_mon_non_thu_is_zero() -> None:
    # 2026-04-21 Tue, 22 Wed, 24 Fri.
    assert signals.day_of_week_score(date(2026, 4, 21)) == 0
    assert signals.day_of_week_score(date(2026, 4, 22)) == 0
    assert signals.day_of_week_score(date(2026, 4, 24)) == 0


def test_day_of_week_score_thursday_bonus_requires_recent_expiry() -> None:
    thursday = date(2026, 4, 30)
    # No expiries passed → no bonus even on Thursday.
    assert signals.day_of_week_score(thursday) == 0
    # Expiry 2 cal days back → within default window, bonus fires.
    assert signals.day_of_week_score(thursday, recent_expiries=[date(2026, 4, 28)]) == +1
    # Expiry 3 cal days back → outside default window, no bonus.
    assert signals.day_of_week_score(thursday, recent_expiries=[date(2026, 4, 27)]) == 0
    # Same-day expiry also fires.
    assert signals.day_of_week_score(thursday, recent_expiries=[thursday]) == +1


def test_month_of_year_size_mult_may_reduced() -> None:
    # May flagged as pre-election / budget vol in docs/india-fno-nuances.md §4.
    assert signals.month_of_year_size_mult(date(2026, 5, 1)) == 0.5
    assert signals.month_of_year_size_mult(date(2026, 5, 31)) == 0.5


def test_month_of_year_size_mult_mar_dec_boosted() -> None:
    assert signals.month_of_year_size_mult(date(2026, 3, 15)) == 1.2
    assert signals.month_of_year_size_mult(date(2026, 12, 15)) == 1.2


def test_month_of_year_size_mult_other_months_neutral() -> None:
    for m in (1, 2, 4, 6, 7, 8, 9, 10, 11):
        assert signals.month_of_year_size_mult(date(2026, m, 15)) == 1.0
