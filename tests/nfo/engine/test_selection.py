"""Tests for engine.selection (master design §6)."""
from __future__ import annotations

import importlib.util
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from nfo.engine.cycles import group_fires_by_cycle
from nfo.engine.selection import (
    select_cycle_matched,
    select_day_matched,
    select_live_rule,
)
from nfo.engine.triggers import TriggerEvaluator
from nfo.specs.loader import load_strategy, reset_registry_for_tests
from nfo.specs.strategy import UniverseSpec


REPO_ROOT = Path(__file__).resolve().parents[3]
SIGNALS = REPO_ROOT / "results" / "nfo" / "historical_signals.parquet"
TRADES = REPO_ROOT / "results" / "nfo" / "spread_trades.csv"


@pytest.fixture
def _iso(tmp_path):
    reset_registry_for_tests(tmp_path / "registry.json")


def _universe() -> UniverseSpec:
    return UniverseSpec(
        underlyings=["NIFTY"], delta_target=0.30, delta_tolerance=0.05,
        width_rule="fixed", width_value=100.0, dte_target=35, dte_tolerance=3,
    )


def test_select_day_matched_filters_by_entry_date():
    trades = pd.DataFrame([
        {"underlying": "NIFTY", "entry_date": "2025-03-24",
         "param_delta": 0.30, "param_width": 100.0},
        {"underlying": "NIFTY", "entry_date": "2025-03-25",
         "param_delta": 0.30, "param_width": 100.0},
        {"underlying": "BANKNIFTY", "entry_date": "2025-03-24",
         "param_delta": 0.30, "param_width": 100.0},
    ])
    out = select_day_matched(
        trades, firing_dates=[date(2025, 3, 24)], universe_spec=_universe(),
    )
    # BANKNIFTY filtered out (not in universe), 2025-03-25 not a firing date
    assert len(out) == 1
    assert out.iloc[0]["entry_date"] == "2025-03-24"
    assert out.iloc[0]["underlying"] == "NIFTY"


def test_select_day_matched_filters_by_delta():
    trades = pd.DataFrame([
        {"underlying": "NIFTY", "entry_date": "2025-03-24",
         "param_delta": 0.30, "param_width": 100.0},
        {"underlying": "NIFTY", "entry_date": "2025-03-24",
         "param_delta": 0.50, "param_width": 100.0},  # out of tolerance
    ])
    out = select_day_matched(
        trades, firing_dates=[date(2025, 3, 24)], universe_spec=_universe(),
    )
    assert len(out) == 1
    assert out.iloc[0]["param_delta"] == 0.30


def test_live_rule_no_longer_raises_not_implemented():
    # P3-E1: select_live_rule is now fully implemented.
    # Calling with empty cycles returns an empty frame, not NotImplementedError.
    out = select_live_rule(
        {}, None, [], client=None, under=None, spot_daily=pd.DataFrame(),
    )
    assert out.empty


# ── Parity: cycle_matched must match robustness.pick_trade_for_expiry ──

def _import_robustness():
    from nfo import robustness
    return robustness


@pytest.mark.skipif(not (SIGNALS.exists() and TRADES.exists()),
                    reason="requires cached signals + trades")
def test_cycle_matched_parity_v3_hte(_iso):
    # Build fires using engine, group to cycles, then select cycle_matched
    strat_path = REPO_ROOT / "configs" / "nfo" / "strategies" / "v3_frozen.yaml"
    spec, _ = load_strategy(strat_path)
    df = pd.read_parquet(SIGNALS)
    df["date"] = pd.to_datetime(df["date"])

    # Engine fire dates (with the same event resolver approach as Bundle A)
    path = REPO_ROOT / "scripts" / "nfo" / "redesign_variants.py"
    _spec_rv = importlib.util.spec_from_file_location("_legacy_rv_sel", path)
    _mod_rv = importlib.util.module_from_spec(_spec_rv)
    sys.modules["_legacy_rv_sel"] = _mod_rv
    _spec_rv.loader.exec_module(_mod_rv)

    def _event_resolver(entry, dte):
        return "high" if not _mod_rv._event_pass(
            entry, dte, severity_high_kinds={"RBI", "FOMC", "BUDGET"}, window_days=10,
        ) else "none"

    ev = TriggerEvaluator(spec, event_resolver=_event_resolver)
    atr = _mod_rv.load_nifty_atr(df["date"])
    fires = ev.fire_dates(df, atr)
    cycles = group_fires_by_cycle(
        fires, df, underlying="NIFTY", strategy_version=spec.strategy_version,
    )

    trades = pd.read_csv(TRADES)
    # Also merge gap file if it exists, so selection has the same trade universe
    # as the legacy robustness path.
    gaps = REPO_ROOT / "results" / "nfo" / "spread_trades_v3_gaps.csv"
    if gaps.exists():
        trades = pd.concat([trades, pd.read_csv(gaps)], ignore_index=True)

    engine_out = select_cycle_matched(trades, cycles, spec, pt_variant="hte")

    # Legacy path: get_v3_matched_trades on the same signals + trades
    robustness = _import_robustness()
    legacy_out = robustness.get_v3_matched_trades(df, trades, "hte")

    # Compare the essential identifying columns (expiry_date + param_pt + param_width
    # + entry_date + pnl_contract). Ignore the enrichment columns added by engine.
    key_cols = ["expiry_date", "param_pt", "param_width", "param_delta", "entry_date", "pnl_contract"]
    engine_key = engine_out[key_cols].sort_values(key_cols).reset_index(drop=True)
    legacy_key = legacy_out[key_cols].sort_values(key_cols).reset_index(drop=True)

    pd.testing.assert_frame_equal(engine_key, legacy_key, check_dtype=False)
