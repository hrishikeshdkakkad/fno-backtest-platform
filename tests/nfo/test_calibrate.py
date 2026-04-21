"""Empirical-POP table, summary stats, and grid-search logic."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from nfo import calibrate


@pytest.fixture(autouse=True)
def _isolate_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(calibrate, "EMPIRICAL_POP_PATH", tmp_path / "pop.parquet")
    monkeypatch.setattr(calibrate, "TUNED_THRESHOLDS_PATH", tmp_path / "tuned.json")


def _synthetic_trades(n: int = 40, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    deltas = rng.choice([-0.20, -0.25, -0.30, -0.35], size=n)
    dtes = rng.choice([28, 35, 42], size=n)
    # Roughly 70% wins at delta 0.30, 85% at 0.20 — realistic for short puts.
    base_wr = 0.70 + 0.8 * (0.35 + deltas)   # delta magnitude → lower win rate
    win_draws = rng.uniform(size=n)
    pnl_per_share = np.where(win_draws < base_wr,
                             rng.uniform(5, 30, size=n),           # win
                             -rng.uniform(40, 120, size=n))        # loss
    return pd.DataFrame({
        "entry_date": pd.to_datetime([date(2025, 1, 1)] * n),
        "expiry_date": pd.to_datetime([date(2025, 2, 1)] * n),
        "entry_delta": deltas,
        "dte_entry": dtes,
        "pnl_per_share": pnl_per_share,
        "pnl_contract": pnl_per_share * 65.0,
        "outcome": np.where(pnl_per_share > 0, "expired_worthless", "partial_loss"),
    })


# ── Empirical POP table ─────────────────────────────────────────────────────


def test_build_empirical_pop_table_non_empty() -> None:
    trades = _synthetic_trades()
    table = calibrate.build_empirical_pop_table(trades)
    assert not table.empty
    assert set(["delta_bucket", "dte_bucket", "n", "wins", "win_rate"]).issubset(table.columns)
    assert (table["win_rate"] >= 0).all() and (table["win_rate"] <= 1).all()


def test_build_empirical_pop_persists(tmp_path: Path) -> None:
    trades = _synthetic_trades()
    calibrate.build_empirical_pop_table(trades)
    assert calibrate.EMPIRICAL_POP_PATH.exists()


def test_lookup_empirical_pop_returns_nearest_bucket() -> None:
    trades = _synthetic_trades()
    calibrate.build_empirical_pop_table(trades)
    got = calibrate.lookup_empirical_pop(delta=-0.30, dte=35)
    assert "win_rate" in got
    assert got["n"] > 0


def test_lookup_empirical_pop_missing_file_is_nan() -> None:
    # No table persisted → graceful NaN.
    got = calibrate.lookup_empirical_pop(delta=-0.30, dte=35)
    import math
    assert math.isnan(got["win_rate"])


# ── Summary stats ───────────────────────────────────────────────────────────


def test_summary_stats_basic() -> None:
    trades = _synthetic_trades(n=40, seed=1)
    s = calibrate.summary_stats(trades)
    assert s.n == 40
    assert 0 <= s.win_rate <= 1
    assert s.worst_cycle_pnl < 0          # synthetic losses are negative
    assert isinstance(s.sharpe, float)


def test_summary_stats_empty_safe() -> None:
    s = calibrate.summary_stats(pd.DataFrame(columns=["pnl_contract"]))
    assert s.n == 0
    assert s.sharpe == 0.0


# ── Grid search ─────────────────────────────────────────────────────────────


def test_grid_search_returns_best_combo() -> None:
    trades = _synthetic_trades(n=60, seed=2)
    # Enrich with synthetic regime signals — monotonic relationship with PnL so
    # the grid can actually find a useful threshold.
    rng = np.random.default_rng(7)
    trades = trades.copy()
    # Winners get richer VIX / IV-RV; losers get lean regimes.
    winners = trades["pnl_per_share"] > 0
    trades["vix"] = np.where(winners, rng.uniform(22, 30, len(trades)), rng.uniform(12, 20, len(trades)))
    trades["vix_pct_3mo"] = np.where(winners, rng.uniform(0.7, 0.95, len(trades)), rng.uniform(0.2, 0.5, len(trades)))
    trades["iv_minus_rv"] = np.where(winners, rng.uniform(2, 6, len(trades)), rng.uniform(-3, 0, len(trades)))
    trades["pullback_atr"] = np.where(winners, rng.uniform(1.5, 3, len(trades)), rng.uniform(0, 0.5, len(trades)))

    result = calibrate.grid_search_thresholds(trades, min_trades=5)
    assert "best" in result and result["best"] is not None
    best_sharpe = result["best"]["sharpe"]
    baseline_sharpe = result["baseline_unfiltered"]["sharpe"]
    assert best_sharpe >= baseline_sharpe    # filter should improve or at worst tie


def test_grid_search_empty_trades() -> None:
    out = calibrate.grid_search_thresholds(pd.DataFrame())
    assert out["best"] is None


def test_load_tuned_thresholds_roundtrip() -> None:
    trades = _synthetic_trades(60, seed=3)
    rng = np.random.default_rng(9)
    trades["vix"] = rng.uniform(15, 25, len(trades))
    trades["vix_pct_3mo"] = rng.uniform(0.3, 0.9, len(trades))
    trades["iv_minus_rv"] = rng.uniform(-2, 5, len(trades))
    trades["pullback_atr"] = rng.uniform(0.2, 2.5, len(trades))
    calibrate.grid_search_thresholds(trades, min_trades=5)
    loaded = calibrate.load_tuned_thresholds()
    assert loaded is not None
    assert "best" in loaded
