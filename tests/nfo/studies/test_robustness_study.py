"""Tests for engine-backed robustness study (P5-D1)."""
from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path

import pandas as pd
import pytest

from nfo.specs.loader import load_strategy, reset_registry_for_tests
from nfo.studies.robustness import RobustnessResult, run_robustness


REPO_ROOT = Path(__file__).resolve().parents[3]
SIGNALS = REPO_ROOT / "results" / "nfo" / "historical_signals.parquet"
TRADES = REPO_ROOT / "results" / "nfo" / "spread_trades.csv"
GAPS = REPO_ROOT / "results" / "nfo" / "spread_trades_v3_gaps.csv"
STRAT_PATH = REPO_ROOT / "configs" / "nfo" / "strategies" / "v3_frozen.yaml"


@pytest.fixture
def _iso(tmp_path):
    reset_registry_for_tests(tmp_path / "registry.json")


def _import_legacy():
    path = REPO_ROOT / "scripts" / "nfo" / "redesign_variants.py"
    spec = importlib.util.spec_from_file_location("_legacy_rv_rob", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_legacy_rv_rob"] = mod
    spec.loader.exec_module(mod)
    return mod


# ── Empty-input edge case ────────────────────────────────────────────────────


def test_empty_features_returns_empty_result(_iso):
    """Empty features_df → empty matched, zero baseline, zero bootstrap."""
    spec, _ = load_strategy(STRAT_PATH)
    empty_features = pd.DataFrame(
        columns=[
            "date", "target_expiry", "vix", "vix_pct_3mo",
            "iv_minus_rv", "iv_rank_12mo", "trend_score",
            "event_risk_v3", "dte",
        ]
    )
    empty_features["date"] = pd.to_datetime(empty_features["date"])
    empty_atr = pd.Series(dtype=float)
    trades = pd.DataFrame(columns=[
        "param_delta", "param_width", "expiry_date",
        "param_pt", "buying_power", "pnl_contract", "outcome",
    ])
    result = run_robustness(
        spec=spec, features_df=empty_features, atr_series=empty_atr,
        trades_df=trades, pt_variant="hte", capital_inr=10_00_000,
        years=1.0, bootstrap_iterations=5, seed=7,
        slippage_sweep_rupees=[0, 250, 500],
    )
    assert isinstance(result, RobustnessResult)
    assert result.matched_trades.empty
    assert result.baseline_stats.n == 0
    assert result.baseline_equity.total_pnl_fixed == 0.0
    # Slippage sweep has one row per level even when matched is empty.
    assert len(result.slippage_sweep) == 3
    assert set(result.slippage_sweep["slippage"].tolist()) == {0.0, 250.0, 500.0}
    assert (result.slippage_sweep["total_pnl_fixed"] == 0.0).all()
    assert result.leave_one_out == []
    # Empty matched → bootstrap returns zero-length arrays (n_iter=0).
    assert result.bootstrap.n_iter == 0
    assert result.years == 1.0
    assert result.capital_inr == 10_00_000.0
    assert result.pt_variant == "hte"


# ── Synthetic 5-trade frame ──────────────────────────────────────────────────


def test_synthetic_slippage_sweep_reduces_pnl(_iso):
    """5 synthetic cycles, slippage sweep with 3 levels → expect monotonic drag."""
    spec, _ = load_strategy(STRAT_PATH)
    # 5 fire days, each on a distinct target_expiry. Gates are all on so V3
    # fires (s3/s6/s8 core + VIX high vol signal).
    features = pd.DataFrame(
        [
            {"date": pd.Timestamp("2024-03-01"), "target_expiry": "2024-03-28",
             "vix": 25.0, "vix_pct_3mo": 0.85, "iv_minus_rv": 1.0,
             "iv_rank_12mo": 0.7, "trend_score": 3.0,
             "event_risk_v3": "none", "dte": 27},
            {"date": pd.Timestamp("2024-04-05"), "target_expiry": "2024-04-25",
             "vix": 22.0, "vix_pct_3mo": 0.9, "iv_minus_rv": 0.5,
             "iv_rank_12mo": 0.65, "trend_score": 2.5,
             "event_risk_v3": "none", "dte": 20},
            {"date": pd.Timestamp("2024-05-10"), "target_expiry": "2024-05-30",
             "vix": 23.0, "vix_pct_3mo": 0.82, "iv_minus_rv": 2.0,
             "iv_rank_12mo": 0.8, "trend_score": 2.5,
             "event_risk_v3": "none", "dte": 20},
            {"date": pd.Timestamp("2024-06-10"), "target_expiry": "2024-06-27",
             "vix": 24.0, "vix_pct_3mo": 0.85, "iv_minus_rv": 1.5,
             "iv_rank_12mo": 0.72, "trend_score": 2.2,
             "event_risk_v3": "none", "dte": 17},
            {"date": pd.Timestamp("2024-07-10"), "target_expiry": "2024-07-25",
             "vix": 26.0, "vix_pct_3mo": 0.92, "iv_minus_rv": 0.8,
             "iv_rank_12mo": 0.68, "trend_score": 2.8,
             "event_risk_v3": "none", "dte": 15},
        ]
    )
    atr = pd.Series(
        [100.0] * 5, index=pd.to_datetime(features["date"]),
    )
    # One matching trade per expiry, all profitable at baseline.
    trades = pd.DataFrame([
        {"param_delta": 0.30, "param_width": 100.0,
         "expiry_date": exp, "param_pt": 1.0,
         "buying_power": 5000.0, "pnl_contract": 1500.0,
         "outcome": "expired_worthless", "entry_date": entry}
        for exp, entry in [
            ("2024-03-28", "2024-03-01"),
            ("2024-04-25", "2024-04-05"),
            ("2024-05-30", "2024-05-10"),
            ("2024-06-27", "2024-06-10"),
            ("2024-07-25", "2024-07-10"),
        ]
    ])
    result = run_robustness(
        spec=spec, features_df=features, atr_series=atr,
        trades_df=trades, pt_variant="hte", capital_inr=10_00_000,
        years=0.5, bootstrap_iterations=50, seed=42,
        slippage_sweep_rupees=[0, 250, 500],
    )
    assert len(result.matched_trades) == 5
    assert result.baseline_stats.n == 5
    # 3 slippage levels → 3 rows.
    assert len(result.slippage_sweep) == 3
    assert list(result.slippage_sweep["slippage"]) == [0.0, 250.0, 500.0]
    # Expected P&L at each level:
    #   baseline pnl_per_lot = 1500; 10L / 5k BP → 200 lots; 5 trades
    #   level 0    : 200 * 1500 * 5 = 1,500,000
    #   level 250  : 200 * 1250 * 5 = 1,250,000
    #   level 500  : 200 * 1000 * 5 = 1,000,000
    sweep = result.slippage_sweep
    assert math.isclose(sweep.iloc[0]["total_pnl_fixed"], 1_500_000.0)
    assert math.isclose(sweep.iloc[1]["total_pnl_fixed"], 1_250_000.0)
    assert math.isclose(sweep.iloc[2]["total_pnl_fixed"], 1_000_000.0)
    # Slippage reduces total P&L monotonically.
    series = sweep["total_pnl_fixed"].to_list()
    assert series[0] > series[1] > series[2]
    # LOO returns one row per trade.
    assert len(result.leave_one_out) == 5
    # Bootstrap ran with n_iter=50.
    assert result.bootstrap.n_iter == 50


# ── Parity against legacy V3 cached data ─────────────────────────────────────


@pytest.mark.skipif(
    not (SIGNALS.exists() and TRADES.exists() and STRAT_PATH.exists()),
    reason="requires committed cached signals + trades",
)
def test_parity_vs_legacy_v3_hte(_iso):
    """run_robustness baseline_stats matches legacy `get_v3_matched_trades`
    + `summary_stats`, and block bootstrap with the same seed produces
    identical total_pnl_fixed percentiles.
    """
    from nfo.robustness import (
        block_bootstrap as _legacy_bootstrap,
        get_v3_matched_trades,
    )
    from nfo.engine.metrics import summary_stats as _engine_summary

    spec, _ = load_strategy(STRAT_PATH)
    df = pd.read_parquet(SIGNALS)
    df["date"] = pd.to_datetime(df["date"])
    trades = pd.read_csv(TRADES)
    if GAPS.exists():
        trades = pd.concat([trades, pd.read_csv(GAPS)], ignore_index=True)

    legacy = _import_legacy()
    atr = legacy.load_nifty_atr(df["date"])

    def _event_resolver(entry, dte):
        return "high" if not legacy._event_pass(
            entry, dte, severity_high_kinds={"RBI", "FOMC", "BUDGET"},
            window_days=10,
        ) else "none"

    # Use a reduced bootstrap count here to keep the test fast; the seed is
    # what matters for parity — legacy and engine must produce the same
    # percentile distribution from the same seed and the same matched trades.
    n_iter = 200
    result = run_robustness(
        spec=spec, features_df=df, atr_series=atr, trades_df=trades,
        pt_variant="hte", capital_inr=10_00_000,
        bootstrap_iterations=n_iter, seed=42,
        slippage_sweep_rupees=[0, 500, 1000],
        event_resolver=_event_resolver,
    )

    legacy_matched = get_v3_matched_trades(df, trades, "hte")
    assert len(result.matched_trades) == len(legacy_matched)

    legacy_stats = _engine_summary(legacy_matched)
    assert math.isclose(
        result.baseline_stats.total_pnl_contract,
        legacy_stats.total_pnl_contract,
        rel_tol=1e-9, abs_tol=1e-9,
    )
    assert math.isclose(
        result.baseline_stats.win_rate,
        legacy_stats.win_rate,
        rel_tol=1e-9, abs_tol=1e-9,
    )
    assert math.isclose(
        result.baseline_stats.sharpe,
        legacy_stats.sharpe,
        rel_tol=1e-9, abs_tol=1e-9,
    )

    # Bootstrap parity: same seed, same matched trades → same percentile ladder.
    start = df["date"].min().date()
    end = df["date"].max().date()
    years = (end - start).days / 365.25
    legacy_boot = _legacy_bootstrap(
        legacy_matched,
        capital=10_00_000, years=years, n_iter=n_iter, seed=42,
    )
    engine_pct = result.bootstrap.percentiles()
    legacy_pct = legacy_boot.percentiles()
    for p in (5, 25, 50, 75, 95):
        a = float(engine_pct.loc[engine_pct["percentile"] == p, "total_pnl_fixed"].iloc[0])
        b = float(legacy_pct.loc[legacy_pct["percentile"] == p, "total_pnl_fixed"].iloc[0])
        assert math.isclose(a, b, rel_tol=1e-6, abs_tol=1.0), (
            f"P{p} total_pnl_fixed drift: engine={a} legacy={b}"
        )
