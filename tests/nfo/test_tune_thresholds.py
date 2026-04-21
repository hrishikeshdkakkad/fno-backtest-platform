"""Pure-math helpers inside `scripts/nfo/tune_thresholds.py`.

The enrichment end-to-end requires cached parquets (NIFTY + VIX), which are
exercised by offline runs, not unit tests. Here we pin only the deterministic
helpers whose behaviour is load-bearing for the live↔tuner alignment.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

import tune_thresholds as tt


# ── _vix_pct_from_history ───────────────────────────────────────────────────


def test_vix_pct_matches_live_ecdf_definition() -> None:
    # Same semantics as `regime_watch._vix_percentile`: fraction of history
    # strictly at-or-below today. With 10 ordered samples and today=7, the
    # ECDF is 7/10 = 0.7.
    hist = np.arange(1.0, 11.0)
    assert tt._vix_pct_from_history(7.0, hist) == pytest.approx(0.7)


def test_vix_pct_returns_nan_on_missing_today() -> None:
    # Trade with no entry VIX — return NaN so the downstream `dropna` drops
    # it instead of silently treating 0.0 as a 0th-percentile pass.
    hist = np.arange(1.0, 11.0)
    assert math.isnan(tt._vix_pct_from_history(float("nan"), hist))


def test_vix_pct_returns_nan_on_empty_history() -> None:
    assert math.isnan(tt._vix_pct_from_history(15.0, np.asarray([], dtype=float)))


# ── _enrich_trades: live↔tuner distribution alignment ──────────────────────


def _synthetic_nifty_daily(n: int = 120, seed: int = 0) -> pd.DataFrame:
    # Geometric-Brownian-ish walk — enough variation for RV / ATR not to
    # return NaN. Not a realistic backtest; just a deterministic input.
    rng = np.random.default_rng(seed)
    rets = rng.normal(0.0002, 0.01, size=n)
    closes = 20000.0 * np.exp(np.cumsum(rets))
    dates = pd.date_range("2024-01-01", periods=n, freq="B")
    return pd.DataFrame({
        "date": dates,
        "open": closes,
        "high": closes * 1.005,
        "low": closes * 0.995,
        "close": closes,
    })


def _synthetic_vix_daily(n: int = 120, seed: int = 1) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    closes = 15.0 + rng.normal(0, 1.5, size=n).cumsum() * 0.05 + rng.normal(0, 0.5, size=n)
    closes = np.clip(closes, 10.0, 35.0)
    dates = pd.date_range("2024-01-01", periods=n, freq="B")
    return pd.DataFrame({
        "date": dates,
        "open": closes, "high": closes + 0.5, "low": closes - 0.5, "close": closes,
    })


def test_enrich_trades_uses_actual_vix_when_cache_present() -> None:
    # With a VIX cache, the tuner must source `vix` from the VIX close on
    # entry_date, NOT from entry_iv. This is the fix's core behaviour: the
    # distribution `vix_pct_3mo` ranks against is the same VIX daily series
    # that regime_watch.py uses live.
    nifty = _synthetic_nifty_daily()
    vix = _synthetic_vix_daily()
    trades = pd.DataFrame({
        "entry_date": [pd.Timestamp("2024-05-01"), pd.Timestamp("2024-05-15")],
        "entry_iv":   [22.5, 19.0],
    })
    out = tt._enrich_trades(trades, nifty, vix)
    # VIX at entry_date, looked up from the VIX parquet — not equal to entry_iv.
    expected_vix_0 = float(vix.loc[vix["date"] <= trades["entry_date"].iloc[0], "close"].iloc[-1])
    expected_vix_1 = float(vix.loc[vix["date"] <= trades["entry_date"].iloc[1], "close"].iloc[-1])
    assert out["vix"].iloc[0] == pytest.approx(expected_vix_0)
    assert out["vix"].iloc[1] == pytest.approx(expected_vix_1)
    # vix_pct_3mo is a percentile → in [0, 1].
    assert 0.0 <= out["vix_pct_3mo"].iloc[0] <= 1.0
    assert 0.0 <= out["vix_pct_3mo"].iloc[1] <= 1.0


def test_enrich_trades_falls_back_when_vix_cache_absent() -> None:
    # Without a VIX cache we log-warn and fall back to the legacy rolling-RV
    # proxy — callers who haven't run refresh_vix_cache.py still get numbers,
    # just not the live-aligned ones. The `vix` column degenerates to entry_iv
    # in this path (legacy behaviour, preserved for continuity).
    nifty = _synthetic_nifty_daily()
    trades = pd.DataFrame({
        "entry_date": [pd.Timestamp("2024-05-01")],
        "entry_iv":   [22.5],
    })
    out = tt._enrich_trades(trades, nifty, vix_daily=None)
    assert out["vix"].iloc[0] == pytest.approx(22.5)   # proxy branch
    # Percentile still computable via the RV fallback — in [0, 1].
    assert 0.0 <= out["vix_pct_3mo"].iloc[0] <= 1.0
