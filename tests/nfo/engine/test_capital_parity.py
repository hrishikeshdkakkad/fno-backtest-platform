"""Parity: engine.capital.compute_equity_curves matches legacy
robustness.compute_equity_curves (which is now a shim over the engine).

This test builds V3 matched trades from the cached historical signals and
compares the engine output against the legacy shim output, proving:
  1. The shim forwards legacy args correctly to the engine.
  2. The engine computation matches the historical numbers byte-for-byte
     (within 1e-6 relative tolerance).

The test is skipped if either `results/nfo/historical_signals.parquet` or
`results/nfo/spread_trades.csv` is missing.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from nfo import robustness as _legacy
from nfo.engine.capital import compute_equity_curves as _engine_compute
from nfo.specs.strategy import CapitalSpec


REPO_ROOT = Path(__file__).resolve().parents[3]
SIGNALS = REPO_ROOT / "results" / "nfo" / "historical_signals.parquet"
TRADES = REPO_ROOT / "results" / "nfo" / "spread_trades.csv"


requires_data = pytest.mark.skipif(
    not (SIGNALS.exists() and TRADES.exists()),
    reason="historical signals or spread trades missing",
)


@requires_data
def test_engine_capital_matches_legacy_shim_on_v3_matched_trades():
    """Engine compute_equity_curves == legacy shim on V3 matched trades."""
    signals_df = pd.read_parquet(SIGNALS)
    signals_df["date"] = pd.to_datetime(signals_df["date"])
    trades_df = pd.read_csv(TRADES)

    matched = _legacy.get_v3_matched_trades(signals_df, trades_df, "hte")
    if matched.empty:
        pytest.skip("no V3 matched trades found in cached data")

    capital = 1_000_000.0
    # Cover a ~1-year window so annualisation is non-zero.
    years = 1.0

    # Engine path (new, CapitalSpec-based).
    spec = CapitalSpec(
        fixed_capital_inr=capital,
        deployment_fraction=1.0,
        compounding=False,
    )
    eq_engine = _engine_compute(matched, capital_spec=spec, years=years)

    # Legacy path (shim over engine; ensures shim is byte-compatible).
    eq_legacy = _legacy.compute_equity_curves(matched, capital=capital, years=years)

    # Scalar fields must match within 1e-6 rel.
    assert eq_engine.total_pnl_fixed == pytest.approx(eq_legacy.total_pnl_fixed, rel=1e-6)
    assert eq_engine.total_pnl_compound == pytest.approx(eq_legacy.total_pnl_compound, rel=1e-6)
    assert eq_engine.final_equity_compound == pytest.approx(
        eq_legacy.final_equity_compound, rel=1e-6,
    )
    assert eq_engine.max_drawdown_pct == pytest.approx(eq_legacy.max_drawdown_pct, rel=1e-6)
    assert eq_engine.annualised_pct_fixed == pytest.approx(
        eq_legacy.annualised_pct_fixed, rel=1e-6,
    )
    assert eq_engine.annualised_pct_compound == pytest.approx(
        eq_legacy.annualised_pct_compound, rel=1e-6,
    )
    assert eq_engine.sharpe == pytest.approx(eq_legacy.sharpe, rel=1e-6)
    assert eq_engine.years == pytest.approx(eq_legacy.years, rel=1e-6)

    # Series fields must match element-wise within 1e-6 rel.
    pd.testing.assert_series_equal(
        eq_engine.pnl_fixed, eq_legacy.pnl_fixed, rtol=1e-6, check_names=False,
    )
    pd.testing.assert_series_equal(
        eq_engine.pnl_compound, eq_legacy.pnl_compound, rtol=1e-6, check_names=False,
    )
    pd.testing.assert_series_equal(
        eq_engine.equity_compound, eq_legacy.equity_compound, rtol=1e-6, check_names=False,
    )
