"""Tests for engine-backed variant_comparison (V3 branch)."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pandas as pd
import pytest

from nfo.specs.loader import load_strategy, reset_registry_for_tests
from nfo.studies.variant_comparison import VariantResult, run_variant_comparison_v3


REPO_ROOT = Path(__file__).resolve().parents[3]
SIGNALS = REPO_ROOT / "results" / "nfo" / "historical_signals.parquet"
TRADES = REPO_ROOT / "results" / "nfo" / "spread_trades.csv"
GAPS = REPO_ROOT / "results" / "nfo" / "spread_trades_v3_gaps.csv"


@pytest.fixture
def _iso(tmp_path):
    reset_registry_for_tests(tmp_path / "registry.json")


def _import_legacy():
    path = REPO_ROOT / "scripts" / "nfo" / "redesign_variants.py"
    spec = importlib.util.spec_from_file_location("_legacy_rv_ve", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_legacy_rv_ve"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.skipif(not (SIGNALS.exists() and TRADES.exists()),
                    reason="requires cached signals + trades")
def test_v3_fires_match_legacy(_iso):
    """Engine V3 firing-day count equals legacy V3 firing-day count.

    This is the tight parity contract: trigger-logic is the engine's job and
    must match legacy exactly. Selection semantics (cycle_matched vs legacy's
    day_matched evaluate_variant) deliberately differ — engine returns one
    trade per cycle at the spec's preferred_exit_variant, which is the proper
    backtest unit; legacy evaluate_variant returns every trade whose entry_date
    is a firing day (many per cycle). n_fires is the apples-to-apples check.
    """
    spec, _ = load_strategy(REPO_ROOT / "configs" / "nfo" / "strategies" / "v3_frozen.yaml")
    df = pd.read_parquet(SIGNALS)
    df["date"] = pd.to_datetime(df["date"])
    trades = pd.read_csv(TRADES)
    if GAPS.exists():
        trades = pd.concat([trades, pd.read_csv(GAPS)], ignore_index=True)

    legacy = _import_legacy()

    def _event_resolver(entry, dte):
        return "high" if not legacy._event_pass(
            entry, dte, severity_high_kinds={"RBI", "FOMC", "BUDGET"}, window_days=10,
        ) else "none"

    atr = legacy.load_nifty_atr(df["date"])

    engine_result: VariantResult = run_variant_comparison_v3(
        spec=spec, features_df=df, atr_series=atr, trades_df=trades,
        event_resolver=_event_resolver,
    )

    # Legacy path: evaluate_variant on V3
    v3_variant = next(v for v in legacy.make_variants() if v.name == "V3")
    legacy_metrics = legacy.evaluate_variant(v3_variant, df, trades, atr)

    # Fire counts must match exactly — trigger logic parity.
    assert engine_result.n_fires == legacy_metrics["firing_days"], (
        f"n_fires: engine={engine_result.n_fires} legacy={legacy_metrics['firing_days']}"
    )

    # Shape checks on engine result.
    assert isinstance(engine_result, VariantResult)
    assert engine_result.name == "V3"
    assert engine_result.n_fires > 0, "V3 should fire at least once over the 2-year window"
    assert engine_result.n_matched_trades >= 0
    assert 0.0 <= engine_result.win_rate <= 1.0
    assert engine_result.firing_rate_per_year > 0
