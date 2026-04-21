"""Tests for studies.falsification.run_falsification."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pandas as pd
import pytest

from nfo.specs.loader import load_strategy
from nfo.studies.falsification import FalsificationResult, run_falsification


REPO_ROOT = Path(__file__).resolve().parents[3]
SIGNALS = REPO_ROOT / "results" / "nfo" / "historical_signals.parquet"
TRADES = REPO_ROOT / "results" / "nfo" / "spread_trades.csv"
GAPS = REPO_ROOT / "results" / "nfo" / "spread_trades_v3_gaps.csv"
STRAT_PATH = REPO_ROOT / "configs" / "nfo" / "strategies" / "v3_frozen.yaml"


@pytest.fixture(autouse=True)
def _real_registry(monkeypatch):
    from nfo.specs import loader
    monkeypatch.setattr(
        loader, "_REGISTRY_PATH",
        REPO_ROOT / "configs" / "nfo" / ".registry.json",
        raising=True,
    )


def _load_rv():
    path = REPO_ROOT / "scripts" / "nfo" / "redesign_variants.py"
    spec = importlib.util.spec_from_file_location("_legacy_rv_fals", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_legacy_rv_fals"] = mod
    spec.loader.exec_module(mod)
    return mod


def _empty_spec_features():
    """Empty features DataFrame for the no-matches scenario."""
    return pd.DataFrame(columns=["date", "target_expiry", "dte"])


@pytest.mark.skipif(
    not (SIGNALS.exists() and TRADES.exists()),
    reason="requires cached signals + trades",
)
def test_run_falsification_produces_all_sections():
    spec, _ = load_strategy(STRAT_PATH)
    df = pd.read_parquet(SIGNALS)
    df["date"] = pd.to_datetime(df["date"])
    trades = pd.read_csv(TRADES)
    if GAPS.exists():
        trades = pd.concat([trades, pd.read_csv(GAPS)], ignore_index=True)
    rv = _load_rv()
    atr = rv.load_nifty_atr(df["date"])

    def _ev(entry, dte):
        return "high" if not rv._event_pass(
            entry, dte, severity_high_kinds={"RBI", "FOMC", "BUDGET"},
            window_days=10,
        ) else "none"

    result = run_falsification(
        spec=spec, features_df=df, atr_series=atr, trades_df=trades,
        pt_variant="hte", capital_inr=1_000_000,
        tail_loss_injections=[1, 2], tail_loss_iterations=10,
        allocation_fractions=[0.5, 1.0], walkforward_folds=3,
        event_resolver=_ev, seed=42,
    )
    assert isinstance(result, FalsificationResult)
    assert not result.matched_trades.empty
    # tail_loss: 2 injection levels × 10 iterations = 20 rows
    assert len(result.tail_loss) == 20
    assert set(result.tail_loss["n_injections"].unique()) == {1, 2}
    # allocation_sweep: 2 rows
    assert len(result.allocation_sweep) == 2
    # walkforward: folds 1..2 = 2 rows (fold 0 skipped)
    assert len(result.walkforward) == 2


def test_run_falsification_empty_matches():
    spec, _ = load_strategy(STRAT_PATH)
    result = run_falsification(
        spec=spec,
        features_df=_empty_spec_features(),
        atr_series=pd.Series(dtype=float),
        trades_df=pd.DataFrame(),
        pt_variant="hte", capital_inr=1_000_000,
        tail_loss_injections=[1], tail_loss_iterations=5,
        allocation_fractions=[1.0], walkforward_folds=2,
        seed=42,
    )
    assert result.matched_trades.empty
    assert result.tail_loss.empty
    assert result.walkforward.empty
    # allocation_sweep can be 1 row with zero values
    assert len(result.allocation_sweep) == 1


@pytest.mark.skipif(
    not (SIGNALS.exists() and TRADES.exists()),
    reason="requires cached signals + trades",
)
def test_run_falsification_deterministic_with_seed():
    """Same seed should produce same tail_loss numbers."""
    spec, _ = load_strategy(STRAT_PATH)
    df = pd.read_parquet(SIGNALS)
    df["date"] = pd.to_datetime(df["date"])
    trades = pd.read_csv(TRADES)
    if GAPS.exists():
        trades = pd.concat([trades, pd.read_csv(GAPS)], ignore_index=True)
    rv = _load_rv()
    atr = rv.load_nifty_atr(df["date"])

    def _ev(entry, dte):
        return "high" if not rv._event_pass(
            entry, dte, severity_high_kinds={"RBI", "FOMC", "BUDGET"},
            window_days=10,
        ) else "none"

    kw = dict(
        spec=spec, features_df=df, atr_series=atr, trades_df=trades,
        pt_variant="hte", capital_inr=1_000_000,
        tail_loss_injections=[1], tail_loss_iterations=20,
        allocation_fractions=[1.0], walkforward_folds=2,
        event_resolver=_ev, seed=42,
    )
    r1 = run_falsification(**kw)
    r2 = run_falsification(**kw)
    pd.testing.assert_frame_equal(r1.tail_loss, r2.tail_loss)
