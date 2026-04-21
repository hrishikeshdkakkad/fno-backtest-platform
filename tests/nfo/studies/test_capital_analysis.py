"""Tests for engine-backed capital_analysis study."""
from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path

import pandas as pd
import pytest

from nfo.specs.loader import load_strategy, reset_registry_for_tests
from nfo.studies.capital_analysis import CapitalAnalysisResult, run_capital_analysis


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
    spec = importlib.util.spec_from_file_location("_legacy_rv_ca", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_legacy_rv_ca"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_empty_features_returns_empty_selection(_iso):
    """Empty features_df → empty selected_trades → zero stats."""
    spec, _ = load_strategy(STRAT_PATH)
    empty_features = pd.DataFrame(
        columns=["date", "target_expiry", "vix", "vix_pct_3mo",
                 "iv_minus_rv", "iv_rank_12mo", "trend_score", "event_risk_v3", "dte"]
    )
    empty_features["date"] = pd.to_datetime(empty_features["date"])
    empty_atr = pd.Series(dtype=float)
    trades = pd.DataFrame(columns=[
        "param_delta", "param_width", "expiry_date",
        "param_pt", "buying_power", "pnl_contract", "outcome",
    ])

    result = run_capital_analysis(
        spec=spec, features_df=empty_features, atr_series=empty_atr,
        trades_df=trades, pt_variant="hte", capital_inr=10_00_000,
        years=1.0,
    )
    assert isinstance(result, CapitalAnalysisResult)
    assert result.selected_trades.empty
    assert result.stats.n == 0
    assert result.stats.win_rate == 0.0
    assert result.equity_result.total_pnl_fixed == 0.0
    assert result.equity_result.total_pnl_compound == 0.0
    assert result.pt_variant == "hte"
    assert result.years == 1.0


def test_synthetic_three_cycles(_iso):
    """Three synthetic firing cycles → select returns 3 rows, stats computed."""
    spec, _ = load_strategy(STRAT_PATH)

    # Three firing days, each on a different target_expiry.
    features = pd.DataFrame(
        [
            # All gates on — V3 specific-pass requires s3+s6+s8 and at least one vol.
            # s3: iv_minus_rv >= -2.0, s6: trend_score >= 2.0, s8: event_risk_v3 != high.
            # Vol: any one of vix>20, vix_pct_3mo>=0.80, iv_rank_12mo>=0.60.
            {"date": pd.Timestamp("2024-03-01"), "target_expiry": "2024-03-28",
             "vix": 25.0, "vix_pct_3mo": 0.85, "iv_minus_rv": 1.0, "iv_rank_12mo": 0.7,
             "trend_score": 3.0, "event_risk_v3": "none", "dte": 27},
            {"date": pd.Timestamp("2024-04-05"), "target_expiry": "2024-04-25",
             "vix": 22.0, "vix_pct_3mo": 0.9, "iv_minus_rv": 0.5, "iv_rank_12mo": 0.65,
             "trend_score": 2.5, "event_risk_v3": "none", "dte": 20},
            {"date": pd.Timestamp("2024-05-10"), "target_expiry": "2024-05-30",
             "vix": 23.0, "vix_pct_3mo": 0.82, "iv_minus_rv": 2.0, "iv_rank_12mo": 0.8,
             "trend_score": 2.5, "event_risk_v3": "none", "dte": 20},
        ]
    )
    atr = pd.Series(
        [100.0, 100.0, 100.0],
        index=pd.to_datetime(features["date"]),
    )
    # Trade universe: one matching row per expiry at delta=0.30, width=100.
    trades = pd.DataFrame(
        [
            {"param_delta": 0.30, "param_width": 100.0, "expiry_date": "2024-03-28",
             "param_pt": 1.0, "buying_power": 5000.0, "pnl_contract": 1000.0,
             "outcome": "expired_worthless", "entry_date": "2024-03-01"},
            {"param_delta": 0.30, "param_width": 100.0, "expiry_date": "2024-04-25",
             "param_pt": 1.0, "buying_power": 6000.0, "pnl_contract": 1500.0,
             "outcome": "expired_worthless", "entry_date": "2024-04-05"},
            {"param_delta": 0.30, "param_width": 100.0, "expiry_date": "2024-05-30",
             "param_pt": 1.0, "buying_power": 4000.0, "pnl_contract": -500.0,
             "outcome": "partial_loss", "entry_date": "2024-05-10"},
        ]
    )

    result = run_capital_analysis(
        spec=spec, features_df=features, atr_series=atr,
        trades_df=trades, pt_variant="hte", capital_inr=10_00_000,
        years=0.2,
    )
    assert len(result.selected_trades) == 3
    assert result.stats.n == 3
    # 2 positive, 1 negative → win_rate = 2/3.
    assert math.isclose(result.stats.win_rate, 2.0 / 3.0, rel_tol=1e-9)
    # Equity result reflects 3 trades of P&L.
    assert len(result.equity_result.pnl_fixed) == 3
    assert result.equity_result.total_pnl_fixed != 0.0


@pytest.mark.skipif(
    not (SIGNALS.exists() and TRADES.exists() and STRAT_PATH.exists()),
    reason="requires cached signals + trades",
)
def test_parity_vs_legacy_hte(_iso):
    """Engine run_capital_analysis matches legacy robustness helpers (hte variant).

    Compares on:
      - total_pnl_fixed, max_drawdown_pct, sharpe, final_equity_compound within 1e-6 rel.
    """
    from nfo.robustness import (
        compute_equity_curves as _legacy_compute,
        get_v3_matched_trades,
    )

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
            entry, dte, severity_high_kinds={"RBI", "FOMC", "BUDGET"}, window_days=10,
        ) else "none"

    result = run_capital_analysis(
        spec=spec, features_df=df, atr_series=atr, trades_df=trades,
        pt_variant="hte", capital_inr=10_00_000,
        event_resolver=_event_resolver,
    )

    # Legacy baseline: identical cycle-matching + equity computation.
    legacy_matched = get_v3_matched_trades(df, trades, "hte")
    start = df["date"].min().date()
    end = df["date"].max().date()
    years = (end - start).days / 365.25
    legacy_equity = _legacy_compute(legacy_matched, capital=10_00_000, years=years)

    engine_equity = result.equity_result

    def _rel_close(a: float, b: float, tol: float = 1e-6) -> bool:
        if math.isclose(a, b, rel_tol=tol, abs_tol=tol):
            return True
        return False

    assert _rel_close(
        engine_equity.total_pnl_fixed, legacy_equity.total_pnl_fixed
    ), f"total_pnl_fixed: engine={engine_equity.total_pnl_fixed} legacy={legacy_equity.total_pnl_fixed}"
    assert _rel_close(
        engine_equity.max_drawdown_pct, legacy_equity.max_drawdown_pct
    ), f"max_drawdown_pct: engine={engine_equity.max_drawdown_pct} legacy={legacy_equity.max_drawdown_pct}"
    assert _rel_close(
        engine_equity.sharpe, legacy_equity.sharpe
    ), f"sharpe: engine={engine_equity.sharpe} legacy={legacy_equity.sharpe}"
    assert _rel_close(
        engine_equity.final_equity_compound, legacy_equity.final_equity_compound
    ), (
        f"final_equity_compound: engine={engine_equity.final_equity_compound} "
        f"legacy={legacy_equity.final_equity_compound}"
    )
    # Same cycle count.
    assert len(result.selected_trades) == len(legacy_matched)
